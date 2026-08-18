"""Data access for documents.

Repositories take an ``AsyncSession`` rather than opening their own. The
session carries the tenant GUC that every RLS policy reads, so a repository
that opened its own would either be unscoped (seeing nothing) or would need to
know the tenant — duplicating the one thing ``tenant_session`` exists to own.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document
from app.db.models.enums import DocumentRole, DocumentStatus, Language, ParseStrategy


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        filename: str,
        mime_type: str,
        size_bytes: int,
        content_sha256: str,
        storage_key: str,
        role: DocumentRole,
        workspace_id: uuid.UUID | None = None,
    ) -> Document:
        document = Document(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            content_sha256=content_sha256,
            storage_key=storage_key,
            role=role,
            status=DocumentStatus.UPLOADED,
        )
        self._session.add(document)
        await self._session.flush()
        return document

    async def get(self, document_id: uuid.UUID) -> Document | None:
        """Fetch one document.

        No tenant filter here, and that is deliberate rather than an oversight:
        RLS already scopes the query to the session's tenant, so another
        tenant's row simply does not exist from this connection's point of
        view. Adding a redundant WHERE would imply the policy is optional.
        """
        return (
            await self._session.execute(
                select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
            )
        ).scalar_one_or_none()

    async def find_by_checksum(self, content_sha256: str) -> Document | None:
        """Content-addressed lookup, for de-duplicating re-uploads.

        Re-parsing an identical 400-page Arabic tender costs real OCR time and
        embedding spend, so an exact match is reused rather than reprocessed.
        """
        return (
            await self._session.execute(
                select(Document).where(
                    Document.content_sha256 == content_sha256,
                    Document.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def list(
        self,
        *,
        workspace_id: uuid.UUID | None = None,
        role: DocumentRole | None = None,
        status: DocumentStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Document], int]:
        """Return one page plus the total, for the document table."""
        conditions = [Document.deleted_at.is_(None)]
        if workspace_id is not None:
            conditions.append(Document.workspace_id == workspace_id)
        if role is not None:
            conditions.append(Document.role == role)
        if status is not None:
            conditions.append(Document.status == status)

        total = (
            await self._session.execute(
                select(func.count()).select_from(Document).where(*conditions)
            )
        ).scalar_one()

        rows = (
            (
                await self._session.execute(
                    select(Document)
                    .where(*conditions)
                    .order_by(Document.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )

        return list(rows), total

    async def set_status(
        self, document_id: uuid.UUID, status: DocumentStatus, *, failure_reason: str | None = None
    ) -> None:
        """Move a document through the ingestion state machine.

        ``failure_reason`` is required by a CHECK constraint whenever the status
        is FAILED, so it is passed through rather than logged and dropped — a
        failed document with no reason is a support ticket nobody can answer.
        """
        document = await self.get(document_id)
        if document is None:
            return
        document.status = status
        if failure_reason is not None:
            document.failure_reason = failure_reason
        if status is DocumentStatus.PARSING:
            document.parsed_at = None

    async def record_ingestion_result(
        self,
        document_id: uuid.UUID,
        *,
        chunk_count: int,
        page_count: int | None,
        language: Language,
        parse_strategy: ParseStrategy | None,
        text_extraction_ratio: float | None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Persist what ingestion learned and mark the document retrievable."""
        document = await self.get(document_id)
        if document is None:
            return
        document.status = DocumentStatus.READY
        document.chunk_count = chunk_count
        document.page_count = page_count
        document.language = language
        document.parse_strategy = parse_strategy
        document.text_extraction_ratio = text_extraction_ratio
        document.indexed_at = datetime.now(UTC)
        document.failure_reason = None
        if metadata:
            document.doc_metadata = {**document.doc_metadata, **metadata}

    async def soft_delete(self, document_id: uuid.UUID) -> bool:
        document = await self.get(document_id)
        if document is None:
            return False
        document.deleted_at = datetime.now(UTC)
        return True
