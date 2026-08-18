"""What happens to a document the worker can never process.

The dangerous outcome is not an error — it is a document that stays at
UPLOADED. The customer sees a status that never changes, and the stalled-upload
sweeper, which exists to rescue genuinely dropped jobs, cannot tell the
difference and re-queues it every five minutes forever.
"""

from __future__ import annotations

import uuid

import pytest
from app.db.models.enums import DocumentRole, DocumentStatus, Language
from app.db.repositories.documents import DocumentRepository
from app.db.session import tenant_session
from app.workers.runner import run_sync
from app.workers.tasks import sweep
from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _app_role(monkeypatch, app_dsn):
    monkeypatch.setenv("POSTGRES_DSN", app_dsn)
    import app.db.session as session_module
    from app.core.config import get_settings

    def _reset() -> None:
        get_settings.cache_clear()
        session_module._engine = None
        session_module._session_factory = None

    _reset()
    yield
    _reset()


async def _document(tenant_id: uuid.UUID) -> uuid.UUID:
    async with tenant_session(tenant_id) as session:
        document = await DocumentRepository(session).create(
            tenant_id=tenant_id,
            filename="unreadable.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            content_sha256=uuid.uuid4().hex * 2,
            storage_key="does/not/exist.pdf",
            role=DocumentRole.KNOWLEDGE_BASE,
        )
        return document.id


def test_an_unreadable_object_ends_terminal_rather_than_stuck(two_tenants_sync, monkeypatch):
    """Retries exhausted must leave a terminal state and a reason.

    Left at UPLOADED, this one unreadable object becomes permanent background
    load — the sweeper re-queues it, the worker fails it, forever — and the
    customer is never told to re-upload.
    """
    tenant_id, _ = two_tenants_sync
    # Driven exactly as Celery drives it: from synchronous code with no ambient
    # event loop. An async test would exercise a path the worker never takes.
    document_id = run_sync(_document(tenant_id))

    import app.workers.tasks.ingest as ingest

    class _DeadStorage:
        def __init__(self, *args, **kwargs) -> None: ...

        def get(self, key):
            raise OSError(f"no such object: {key}")

    def _exhausted(*args, **kwargs):
        """Stands in for a retry budget that is already spent."""
        raise MaxRetriesExceededError("out of retries")

    monkeypatch.setattr(ingest, "ObjectStorage", _DeadStorage)
    # Patched on the real task rather than faked wholesale, so the test drives
    # the same object Celery drives.
    monkeypatch.setattr(ingest.parse_document_task, "retry", _exhausted)

    with pytest.raises(MaxRetriesExceededError):
        ingest.parse_document_task.run(
            document_id=str(document_id),
            tenant_id=str(tenant_id),
            storage_key="does/not/exist.pdf",
            filename="unreadable.pdf",
            mime_type="application/pdf",
            role=DocumentRole.KNOWLEDGE_BASE.value,
        )

    async def _read():
        async with tenant_session(tenant_id) as session:
            return await DocumentRepository(session).get(document_id)

    stored = run_sync(_read())
    assert stored.status is DocumentStatus.FAILED
    # The reason is what turns a support ticket into a re-upload.
    assert "could not be read" in (stored.failure_reason or "")


async def test_a_failed_document_is_not_re_queued_by_the_sweeper(two_tenants, monkeypatch):
    """The other half of the same guarantee: once terminal, it stays out of
    the queue no matter how old it gets."""
    tenant_id, _ = two_tenants
    document_id = await _document(tenant_id)

    async with tenant_session(tenant_id) as session:
        await DocumentRepository(session).set_status(
            document_id, DocumentStatus.FAILED, failure_reason="unreadable"
        )
        await session.execute(
            text(
                "UPDATE documents SET updated_at = now() - make_interval(secs => :age) "
                "WHERE id = :id"
            ),
            {"age": sweep.STALE_AFTER_SECONDS * 10, "id": document_id},
        )

    sent: list[dict] = []

    class _Task:
        @staticmethod
        def delay(**kwargs):
            sent.append(kwargs)

    import app.workers.tasks.ingest as ingest_module

    monkeypatch.setattr(ingest_module, "parse_document_task", _Task)

    await sweep._run()

    assert [job for job in sent if job["document_id"] == str(document_id)] == []


def test_a_successful_ingestion_is_actually_written(two_tenants_sync, monkeypatch):
    """The path that was never implemented.

    `_persist` was a stub that logged and returned, with a signature its own
    call sites no longer matched — so a document that parsed, chunked, and
    indexed perfectly still never left UPLOADED. Everything downstream depends
    on this row: retrieval filters on READY, the UI polls it, and the sweeper
    treats UPLOADED as work to redo.
    """
    tenant_id, _ = two_tenants_sync
    document_id = run_sync(_document(tenant_id))

    import app.workers.tasks.ingest as ingest

    result = ingest.IngestionResult(
        document_id=document_id,
        status=DocumentStatus.READY,
        chunk_count=1038,
        page_count=214,
        language=Language.AR,
        text_extraction_ratio=0.72,
        parse_strategy="hi_res",
        table_count=17,
    )

    ingest._persist(str(document_id), str(tenant_id), DocumentStatus.READY, None, result)

    async def _read():
        async with tenant_session(tenant_id) as session:
            return await DocumentRepository(session).get(document_id)

    stored = run_sync(_read())
    assert stored.status is DocumentStatus.READY
    assert stored.chunk_count == 1038
    assert stored.page_count == 214
    assert stored.language is Language.AR
    # The Arabic-scan quality signal, which is the whole reason a reviewer can
    # tell a good ingestion from a silently near-empty one.
    assert stored.text_extraction_ratio == pytest.approx(0.72)
    assert stored.doc_metadata["table_count"] == 17
    assert stored.indexed_at is not None


def test_a_failure_with_no_stated_reason_still_gets_recorded(two_tenants_sync):
    """A CHECK constraint refuses a FAILED row with no reason.

    Letting that abort the write would leave the document at UPLOADED — the
    exact state that means "still working" — so a missing reason is filled
    rather than allowed to lose the outcome.
    """
    tenant_id, _ = two_tenants_sync
    document_id = run_sync(_document(tenant_id))

    import app.workers.tasks.ingest as ingest

    ingest._persist(str(document_id), str(tenant_id), DocumentStatus.FAILED, None)

    async def _read():
        async with tenant_session(tenant_id) as session:
            return await DocumentRepository(session).get(document_id)

    stored = run_sync(_read())
    assert stored.status is DocumentStatus.FAILED
    assert stored.failure_reason


def test_two_jobs_in_one_process_both_persist(two_tenants_sync):
    """A worker handles task after task in the same process.

    The engine is process-wide but bound to whichever loop first used it, and
    every task body opens a new one — so without disposal the second job would
    borrow connections from a dead loop and fail with "attached to a different
    loop", writing nothing while the ingestion itself succeeded.
    """
    tenant_id, _ = two_tenants_sync
    first = run_sync(_document(tenant_id))
    second = run_sync(_document(tenant_id))

    import app.workers.tasks.ingest as ingest

    for document_id in (first, second):
        ingest._persist(str(document_id), str(tenant_id), DocumentStatus.FAILED, "unreadable")

    async def _read():
        async with tenant_session(tenant_id) as session:
            repo = DocumentRepository(session)
            return [await repo.get(first), await repo.get(second)]

    stored = run_sync(_read())
    assert [d.status for d in stored] == [DocumentStatus.FAILED, DocumentStatus.FAILED]
