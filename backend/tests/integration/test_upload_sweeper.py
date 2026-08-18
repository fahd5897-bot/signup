"""The sweeper that stops a broker outage from losing an upload silently.

An upload commits its object and its row before queueing the ingestion job, so
a broker failure at that moment leaves a document at UPLOADED with nothing
coming for it. That is the worst kind of failure: nothing errors, the customer
sees a status that never changes, and the file is never searchable.
"""

from __future__ import annotations

import uuid

import pytest
from app.db.models.enums import DocumentRole, DocumentStatus
from app.db.repositories.documents import DocumentRepository
from app.db.session import tenant_session
from app.workers.tasks import sweep
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _app_role(monkeypatch, app_dsn):
    """Runs as the unprivileged role, which is the point.

    The sweep reaches across tenants through a SECURITY DEFINER function, not
    through a privileged role — if this suite ran as the superuser it would
    pass whether or not that function exists.
    """
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


async def _stalled_document(tenant_id: uuid.UUID, *, age_seconds: int) -> uuid.UUID:
    """A document left at UPLOADED, aged by rewriting its timestamp."""
    async with tenant_session(tenant_id) as session:
        document = await DocumentRepository(session).create(
            tenant_id=tenant_id,
            filename="كراسة الشروط.pdf",
            mime_type="application/pdf",
            size_bytes=2048,
            content_sha256=uuid.uuid4().hex * 2,
            storage_key=f"{tenant_id}/stalled.pdf",
            role=DocumentRole.TENDER,
        )
        document_id = document.id
        await session.flush()
        await session.execute(
            text(
                "UPDATE documents SET updated_at = now() - make_interval(secs => :age) "
                "WHERE id = :id"
            ),
            {"age": age_seconds, "id": document_id},
        )
    return document_id


@pytest.fixture
def captured(monkeypatch) -> list[dict]:
    """Record what the sweep hands to Celery instead of reaching a broker."""
    sent: list[dict] = []

    class _Task:
        @staticmethod
        def delay(**kwargs):
            sent.append(kwargs)

    import app.workers.tasks.ingest as ingest_module

    monkeypatch.setattr(ingest_module, "parse_document_task", _Task)
    return sent


async def test_a_stalled_upload_is_picked_up(two_tenants, captured):
    tenant_id, _ = two_tenants
    document_id = await _stalled_document(tenant_id, age_seconds=sweep.STALE_AFTER_SECONDS + 60)

    result = await sweep._run()

    assert result["requeued"] >= 1
    queued = [job for job in captured if job["document_id"] == str(document_id)]
    assert len(queued) == 1
    # Everything the worker needs to find the bytes again.
    assert queued[0]["tenant_id"] == str(tenant_id)
    assert queued[0]["storage_key"].endswith("stalled.pdf")
    assert queued[0]["role"] == DocumentRole.TENDER.value


async def test_a_recent_upload_is_left_alone(two_tenants, captured):
    """A healthy queue must not be re-queued underneath itself: the worker may
    be seconds from picking the job up, and a duplicate re-does the OCR."""
    tenant_id, _ = two_tenants
    document_id = await _stalled_document(tenant_id, age_seconds=5)

    await sweep._run()

    assert [job for job in captured if job["document_id"] == str(document_id)] == []


async def test_a_document_that_finished_is_not_re_queued(two_tenants, captured):
    tenant_id, _ = two_tenants
    document_id = await _stalled_document(tenant_id, age_seconds=sweep.STALE_AFTER_SECONDS + 60)
    async with tenant_session(tenant_id) as session:
        await DocumentRepository(session).set_status(document_id, DocumentStatus.READY)
        await session.execute(
            text(
                "UPDATE documents SET updated_at = now() - make_interval(secs => :age) "
                "WHERE id = :id"
            ),
            {"age": sweep.STALE_AFTER_SECONDS + 60, "id": document_id},
        )

    await sweep._run()

    assert [job for job in captured if job["document_id"] == str(document_id)] == []


async def test_the_sweep_stops_when_the_broker_is_still_down(two_tenants, monkeypatch):
    """Grinding through the batch would log the same failure hundreds of times
    and delay recovery; the next run picks up where this one stopped."""
    tenant_id, _ = two_tenants
    for _ in range(3):
        await _stalled_document(tenant_id, age_seconds=sweep.STALE_AFTER_SECONDS + 60)

    attempts = {"count": 0}

    class _DeadBroker:
        @staticmethod
        def delay(**kwargs):
            attempts["count"] += 1
            raise ConnectionError("broker unreachable")

    import app.workers.tasks.ingest as ingest_module

    monkeypatch.setattr(ingest_module, "parse_document_task", _DeadBroker)

    result = await sweep._run()

    assert attempts["count"] == 1, "stopped after the first failure"
    assert result["requeued"] == 0
    assert result["failed"] == 1
    assert result["found"] >= 3


async def test_the_sweep_sees_documents_from_every_tenant(two_tenants, captured):
    """It cannot know which tenants have stuck uploads before it looks, which
    is exactly why it goes through the SECURITY DEFINER function."""
    a, b = two_tenants
    first = await _stalled_document(a, age_seconds=sweep.STALE_AFTER_SECONDS + 60)
    second = await _stalled_document(b, age_seconds=sweep.STALE_AFTER_SECONDS + 60)

    await sweep._run()

    queued = {job["document_id"] for job in captured}
    assert str(first) in queued
    assert str(second) in queued
