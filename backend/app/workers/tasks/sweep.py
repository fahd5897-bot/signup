"""Periodic repair: pick up work the broker dropped.

An upload commits the object and the row before it queues the ingestion job.
That order is deliberate — the reverse leaves a queued job pointing at bytes
that do not exist — but it means a broker outage at exactly the wrong moment
leaves a document at UPLOADED with nothing coming for it. The customer sees a
status that never changes and a file that is never searchable, and nothing in
the system is wrong enough to raise an alert.

This is the task that closes that hole. It is the only reason `_enqueue` is
allowed to swallow broker failures.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import privileged_session
from app.workers.celery_app import celery_app
from app.workers.runner import run_sync

logger = logging.getLogger(__name__)

#: How long a document may sit at UPLOADED before it is assumed dropped.
#: Comfortably longer than the gap between the row committing and the worker
#: picking the job up, so a healthy queue is never re-queued underneath itself.
STALE_AFTER_SECONDS = 300

#: Ceiling per run. A broker outage can strand thousands of documents; feeding
#: them all back in one burst would knock the recovering broker over again.
BATCH_LIMIT = 200


@celery_app.task(name="sweep.requeue_stalled_uploads")
def requeue_stalled_uploads() -> dict[str, int]:
    """Re-queue documents stuck at UPLOADED. Returns a count for the logs."""
    return run_sync(_run())


async def _run() -> dict[str, int]:
    from app.workers.tasks.ingest import parse_document_task

    settings = get_settings()
    async with privileged_session(settings) as session:
        # A SECURITY DEFINER function, because the sweep is inherently
        # cross-tenant and cannot know which tenants have stuck uploads before
        # it looks. See the migration for why this is narrower than the
        # alternatives.
        rows = (
            await session.execute(
                text("SELECT * FROM stale_uploaded_documents(:older_than, :limit_count)"),
                {"older_than": STALE_AFTER_SECONDS, "limit_count": BATCH_LIMIT},
            )
        ).all()

    requeued = 0
    failed = 0
    for row in rows:
        try:
            parse_document_task.delay(
                document_id=str(row.document_id),
                tenant_id=str(row.tenant_id),
                storage_key=row.storage_key,
                filename=row.filename,
                mime_type=row.mime_type,
                role=row.role,
                workspace_id=str(row.workspace_id) if row.workspace_id else None,
            )
            requeued += 1
        except Exception as exc:  # noqa: BLE001 - the broker is what we suspect
            # Still down. Stop rather than grinding through the whole batch
            # logging the same failure two hundred times; the next run retries.
            logger.warning("sweep could not re-queue %s: %s", row.document_id, exc)
            failed += 1
            break

    if requeued:
        logger.info("sweep re-queued %d stalled uploads", requeued)
    return {"requeued": requeued, "failed": failed, "found": len(rows)}


__all__ = ["BATCH_LIMIT", "STALE_AFTER_SECONDS", "requeue_stalled_uploads"]
