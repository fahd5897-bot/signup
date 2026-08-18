"""Ingestion task.

Thin by design: fetch bytes, run the pipeline, persist the outcome. All parsing
logic lives in :mod:`app.ingestion.pipeline`, which knows nothing about Celery
and is therefore testable without a broker.
"""

from __future__ import annotations

import logging
import uuid

from celery import Task
from celery.exceptions import MaxRetriesExceededError, SoftTimeLimitExceeded

from app.core.config import get_settings
from app.db.models.enums import DocumentRole, DocumentStatus
from app.ingestion.pipeline import IngestionPipeline, IngestionResult
from app.services.storage import ObjectStorage
from app.workers.celery_app import celery_app
from app.workers.runner import run_sync

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="ingest.parse_document",
    max_retries=3,
    # Jittered backoff. Without jitter, a Qdrant restart makes every queued
    # document retry in lockstep and knock it over again on recovery.
    retry_backoff=30,
    retry_backoff_max=600,
    retry_jitter=True,
)
def parse_document_task(
    self: Task,
    document_id: str,
    tenant_id: str,
    storage_key: str,
    filename: str,
    mime_type: str,
    role: str,
    workspace_id: str | None = None,
) -> dict[str, object]:
    """Ingest one stored document.

    Arguments are primitives, not ORM objects: task payloads are JSON on the
    wire, and a serialised entity would be stale by the time it is consumed.
    """
    settings = get_settings()
    logger.info("ingesting document %s for tenant %s", document_id, tenant_id)

    try:
        data = ObjectStorage(settings).get(storage_key)
    except Exception as exc:
        # Storage failures are transient far more often than not; let Celery
        # back off rather than marking the document permanently failed.
        logger.warning("could not fetch %s: %s", storage_key, exc)
        try:
            raise self.retry(exc=exc) from exc
        except MaxRetriesExceededError:
            # Retries exhausted. Without this the document stays at UPLOADED
            # forever — and the stalled-upload sweeper, seeing exactly that,
            # re-queues it every five minutes for good, turning one unreadable
            # object into permanent background load. A terminal state is also
            # what tells the customer to re-upload instead of waiting.
            _persist(
                document_id,
                tenant_id,
                DocumentStatus.FAILED,
                f"the stored file could not be read after repeated attempts: {exc}",
            )
            raise

    async def _report(doc_id: uuid.UUID, stage: DocumentStatus) -> None:
        """Surface the live stage so the UI shows "Parsing" rather than a
        spinner that could mean anything for several minutes."""
        from app.services.document_service import DocumentService

        await DocumentService(settings).mark_status(
            tenant_id=uuid.UUID(tenant_id), document_id=doc_id, status=stage
        )

    try:
        result = run_sync(
            _run_pipeline(
                status_callback=_report,
                data=data,
                document_id=uuid.UUID(document_id),
                tenant_id=uuid.UUID(tenant_id),
                filename=filename,
                mime_type=mime_type,
                role=DocumentRole(role),
                workspace_id=uuid.UUID(workspace_id) if workspace_id else None,
            )
        )
    except SoftTimeLimitExceeded as exc:
        # Soft limit fires first precisely so this branch can run and leave the
        # document in a terminal state; the hard limit kills the process.
        logger.error("ingestion of %s exceeded the time limit", document_id)
        _persist(document_id, tenant_id, DocumentStatus.FAILED, "processing timed out")
        raise exc

    if result.succeeded:
        _persist(document_id, tenant_id, DocumentStatus.READY, None, result)
        return {"status": "ready", "chunks": result.chunk_count}

    if result.is_retryable and self.request.retries < settings.ingest_max_retries:
        logger.info(
            "retrying %s (attempt %d): %s",
            document_id,
            self.request.retries + 1,
            result.failure_reason,
        )
        raise self.retry(countdown=30 * (2**self.request.retries))

    # Terminal: either non-retryable, or retries exhausted. Persisting the
    # reason is what turns a stuck spinner into an actionable message.
    _persist(document_id, tenant_id, DocumentStatus.FAILED, result.failure_reason, result)
    return {"status": "failed", "reason": result.failure_reason, "slug": result.failure_slug}


async def _run_pipeline(status_callback=None, **kwargs) -> IngestionResult:
    from app.rag.vectorstore.collections import build_client

    settings = get_settings()
    client = await build_client(settings)
    try:
        pipeline = IngestionPipeline(client, settings, status_callback=status_callback)
        return await pipeline.run(**kwargs)
    finally:
        await client.close()


def _persist(
    document_id: str,
    tenant_id: str,
    status: DocumentStatus,
    failure_reason: str | None,
    result: IngestionResult | None = None,
) -> None:
    """Write the outcome back to the documents row.

    Every exit from this task goes through here, and a failure to write is
    itself logged rather than raised: the work is already done, the vectors are
    already in Qdrant, and re-running the whole ingestion because the status
    write failed would repay minutes of OCR for a row update.

    ``run_sync`` because the task is synchronous — Celery's prefork pool has no
    event loop, and the service layer is async all the way down.
    """
    from app.services.document_service import DocumentService

    logger.info(
        "document %s -> %s%s",
        document_id,
        status.value,
        f" ({failure_reason})" if failure_reason else "",
    )

    service = DocumentService(get_settings())
    document_uuid = uuid.UUID(document_id)
    tenant_uuid = uuid.UUID(tenant_id)

    async def _write() -> None:
        if status is DocumentStatus.READY and result is not None:
            await service.record_success(
                tenant_id=tenant_uuid,
                document_id=document_uuid,
                chunk_count=result.chunk_count,
                page_count=result.page_count,
                language=result.language,
                parse_strategy=result.parse_strategy,
                text_extraction_ratio=result.text_extraction_ratio,
                table_count=result.table_count,
            )
            return
        await service.mark_status(
            tenant_id=tenant_uuid,
            document_id=document_uuid,
            status=status,
            # The CHECK constraint refuses a FAILED row with no reason, so a
            # missing one is filled rather than allowed to abort the write and
            # leave the document looking like it is still processing.
            failure_reason=failure_reason
            or (
                "ingestion failed without a stated reason"
                if status is DocumentStatus.FAILED
                else None
            ),
        )

    try:
        run_sync(_write())
    except Exception as exc:  # noqa: BLE001 - the outcome matters more than the write
        logger.error("could not record %s for document %s: %s", status.value, document_id, exc)
