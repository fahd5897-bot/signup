"""Document lifecycle: register, track, list.

Owns the transaction boundary between the database row and the queued Celery
job. Those two must commit together — a row with no queued work sits at
UPLOADED forever and looks identical to "still processing", while queued work
with no row fails on its first status write.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.core.exceptions import DuplicateDocumentError, TenantMismatchError
from app.db.models.document import Document
from app.db.models.enums import DocumentRole, DocumentStatus, Language, ParseStrategy
from app.db.repositories.documents import DocumentRepository
from app.db.session import tenant_session
from app.services.storage import ObjectStorage, build_storage_key, sha256_bytes

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RegisteredDocument:
    document_id: uuid.UUID
    status: DocumentStatus
    is_duplicate: bool
    task_id: str | None = None


class DocumentService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def register_upload(
        self,
        *,
        tenant_id: uuid.UUID,
        data: bytes,
        filename: str,
        mime_type: str,
        role: DocumentRole,
        workspace_id: uuid.UUID | None = None,
        enqueue: bool = True,
    ) -> RegisteredDocument:
        """Store the bytes, write the row, and queue ingestion.

        Order matters. Object storage is written *before* the row, so a crash
        between the two leaves an orphaned blob — cheap, and swept by the
        retention job. The reverse order leaves a row pointing at bytes that do
        not exist, which fails at parse time and looks like a corrupt upload.
        """
        digest = sha256_bytes(data)

        async with tenant_session(tenant_id, self._settings) as session:
            repository = DocumentRepository(session)

            # Content-addressed de-duplication. Re-uploading an identical
            # 400-page Arabic tender would otherwise repay the OCR and
            # embedding cost for a byte-identical result.
            existing = await repository.find_by_checksum(digest)
            if existing is not None:
                logger.info("document %s is a duplicate of %s", filename, existing.id)
                return RegisteredDocument(
                    document_id=existing.id, status=existing.status, is_duplicate=True
                )

            document_id = uuid.uuid4()
            storage_key = build_storage_key(str(tenant_id), str(document_id), filename)

            ObjectStorage(self._settings).put(storage_key, data, content_type=mime_type)

            document = await repository.create(
                tenant_id=tenant_id,
                filename=filename,
                mime_type=mime_type,
                size_bytes=len(data),
                content_sha256=digest,
                storage_key=storage_key,
                role=role,
                workspace_id=workspace_id,
            )
            # Assign the id we already used to build the storage key, so the
            # key and the row agree.
            document.id = document_id
            await session.flush()

            task_id = None
            if enqueue:
                task_id = self._enqueue(
                    document_id=document_id,
                    tenant_id=tenant_id,
                    storage_key=storage_key,
                    filename=filename,
                    mime_type=mime_type,
                    role=role,
                    workspace_id=workspace_id,
                )

            logger.info("registered document %s for tenant %s", document_id, tenant_id)
            return RegisteredDocument(
                document_id=document_id,
                status=DocumentStatus.UPLOADED,
                is_duplicate=False,
                task_id=task_id,
            )

    def _enqueue(
        self,
        *,
        document_id: uuid.UUID,
        tenant_id: uuid.UUID,
        storage_key: str,
        filename: str,
        mime_type: str,
        role: DocumentRole,
        workspace_id: uuid.UUID | None,
    ) -> str:
        """Hand the document to Celery.

        Broker failures are logged and swallowed rather than raised: the bytes
        and the row are already committed, so failing the request would tell
        the user their upload was lost when it was not. The document stays at
        UPLOADED and is picked up by the sweeper.
        """
        try:
            from app.workers.tasks.ingest import parse_document_task

            result = parse_document_task.delay(
                document_id=str(document_id),
                tenant_id=str(tenant_id),
                storage_key=storage_key,
                filename=filename,
                mime_type=mime_type,
                role=role.value,
                workspace_id=str(workspace_id) if workspace_id else None,
            )
            return result.id
        except Exception as exc:
            logger.error(
                "could not queue ingestion for %s (%s); document stays UPLOADED",
                document_id,
                exc,
            )
            return f"unqueued:{document_id}"

    async def get_status(self, *, tenant_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        """Fetch one document for the status poller.

        A row belonging to another tenant is simply invisible under RLS, so it
        raises the same "not found" as a genuinely missing id — which is the
        intended behaviour: a 403 would confirm the id exists.
        """
        async with tenant_session(tenant_id, self._settings) as session:
            document = await DocumentRepository(session).get(document_id)
            if document is None:
                raise TenantMismatchError(f"document {document_id} not found")
            return document

    async def list_documents(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        role: DocumentRole | None = None,
        status: DocumentStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Document], int]:
        async with tenant_session(tenant_id, self._settings) as session:
            return await DocumentRepository(session).list(
                workspace_id=workspace_id,
                role=role,
                status=status,
                limit=limit,
                offset=offset,
            )

    # ------------------------------------------------------ worker callbacks
    async def mark_status(
        self,
        *,
        tenant_id: uuid.UUID,
        document_id: uuid.UUID,
        status: DocumentStatus,
        failure_reason: str | None = None,
    ) -> None:
        """Called from the ingestion worker as the document moves stages."""
        async with tenant_session(tenant_id, self._settings) as session:
            await DocumentRepository(session).set_status(
                document_id, status, failure_reason=failure_reason
            )

    async def record_success(
        self,
        *,
        tenant_id: uuid.UUID,
        document_id: uuid.UUID,
        chunk_count: int,
        page_count: int | None,
        language: Language,
        parse_strategy: str | None,
        text_extraction_ratio: float | None,
        table_count: int = 0,
    ) -> None:
        async with tenant_session(tenant_id, self._settings) as session:
            await DocumentRepository(session).record_ingestion_result(
                document_id,
                chunk_count=chunk_count,
                page_count=page_count,
                language=language,
                parse_strategy=ParseStrategy(parse_strategy) if parse_strategy else None,
                text_extraction_ratio=text_extraction_ratio,
                metadata={"table_count": table_count},
            )

    async def delete(self, *, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """Soft-delete the row and purge the vectors.

        Vectors go first. A soft-deleted row whose chunks are still indexed
        remains citable evidence for a document the customer believes they
        removed — the failure that matters here is leaving data reachable, not
        leaving a tombstone behind.
        """
        from app.rag.vectorstore.collections import QdrantCollectionManager, build_client

        client = await build_client(self._settings)
        try:
            await QdrantCollectionManager(client, self._settings).delete_document_data(
                str(tenant_id), str(document_id)
            )
        finally:
            await client.close()

        async with tenant_session(tenant_id, self._settings) as session:
            if not await DocumentRepository(session).soft_delete(document_id):
                raise TenantMismatchError(f"document {document_id} not found")


__all__ = ["DocumentService", "RegisteredDocument", "DuplicateDocumentError"]
