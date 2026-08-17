"""Ingestion orchestration: bytes in, retrievable vectors out.

Stages run in order, each updating ``documents.status`` so the UI can show real
progress rather than an indefinite spinner:

    UPLOADED -> PARSING -> CHUNKING -> EMBEDDING -> READY
                                                 \\-> FAILED (with a reason)

The orchestrator owns error classification and status transitions. The stage
functions stay pure — bytes to elements, elements to chunks, chunks to vectors —
which is what makes each of them testable without a database.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from qdrant_client import AsyncQdrantClient, models

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AppError,
    EmptyExtractionError,
    IngestionError,
    VectorStoreError,
)
from app.db.models.enums import DocumentRole, DocumentStatus, Language
from app.ingestion.chunking.semantic_chunker import Chunk, chunk_elements
from app.ingestion.parsers.unstructured_parser import parse_document
from app.rag.embeddings import get_embedding_provider, get_sparse_encoder
from app.rag.vectorstore.schema import ChunkPayload, VectorName

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestionResult:
    """Outcome of one document's ingestion."""

    document_id: uuid.UUID
    status: DocumentStatus
    chunk_count: int = 0
    page_count: int | None = None
    language: Language = Language.UNKNOWN
    text_extraction_ratio: float | None = None
    parse_strategy: str | None = None
    table_count: int = 0
    embedding_tokens: int = 0
    warnings: list[str] | None = None
    failure_reason: str | None = None
    failure_slug: str | None = None
    is_retryable: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status is DocumentStatus.READY


class IngestionPipeline:
    """Runs a document from raw bytes to indexed vectors."""

    def __init__(
        self,
        qdrant: AsyncQdrantClient,
        settings: Settings | None = None,
        *,
        status_callback=None,
    ) -> None:
        self._qdrant = qdrant
        self._settings = settings or get_settings()
        #: Optional ``async (document_id, status) -> None`` hook, so the
        #: pipeline can report progress without importing the database layer.
        self._status_callback = status_callback

    async def run(
        self,
        *,
        data: bytes,
        document_id: uuid.UUID,
        tenant_id: uuid.UUID,
        filename: str,
        mime_type: str,
        role: DocumentRole,
        workspace_id: uuid.UUID | None = None,
    ) -> IngestionResult:
        """Ingest one document. Never raises; failures are returned.

        Returning rather than raising is deliberate: the caller is a Celery task
        whose job is to persist the outcome either way. An exception escaping
        here would leave the document stuck in a transient status forever, which
        in the UI is indistinguishable from "still working".
        """
        try:
            return await self._run(
                data=data,
                document_id=document_id,
                tenant_id=tenant_id,
                filename=filename,
                mime_type=mime_type,
                role=role,
                workspace_id=workspace_id,
            )
        except AppError as exc:
            logger.warning("ingestion failed for %s (%s): %s", document_id, exc.slug, exc.detail)
            return IngestionResult(
                document_id=document_id,
                status=DocumentStatus.FAILED,
                failure_reason=exc.detail,
                failure_slug=exc.slug,
                is_retryable=exc.is_retryable,
            )
        except Exception as exc:  # unexpected: retry, but record it
            logger.exception("unexpected ingestion failure for %s", document_id)
            return IngestionResult(
                document_id=document_id,
                status=DocumentStatus.FAILED,
                failure_reason=f"unexpected error: {exc}",
                failure_slug="internal_error",
                is_retryable=True,
            )

    # ------------------------------------------------------------------ stages
    async def _run(
        self,
        *,
        data: bytes,
        document_id: uuid.UUID,
        tenant_id: uuid.UUID,
        filename: str,
        mime_type: str,
        role: DocumentRole,
        workspace_id: uuid.UUID | None,
    ) -> IngestionResult:
        # --- parse -----------------------------------------------------------
        await self._set_status(document_id, DocumentStatus.PARSING)
        parsed = parse_document(data, filename=filename, mime_type=mime_type)

        # --- chunk -----------------------------------------------------------
        await self._set_status(document_id, DocumentStatus.CHUNKING)
        chunks = chunk_elements(parsed.elements)
        if not chunks:
            raise EmptyExtractionError(
                f"{filename!r} produced no chunks despite {parsed.total_chars} characters of text"
            )

        # --- embed -----------------------------------------------------------
        await self._set_status(document_id, DocumentStatus.EMBEDDING)
        provider = get_embedding_provider(self._settings)
        dense = await provider.embed_documents([c.text for c in chunks])

        sparse_pairs = get_sparse_encoder().encode_documents([c.text for c in chunks])
        if len(sparse_pairs) != len(chunks):
            raise IngestionError(
                f"sparse encoder returned {len(sparse_pairs)} vectors for {len(chunks)} chunks"
            )

        # --- upsert ----------------------------------------------------------
        # Replace-before-insert: re-ingesting a document (retry, or a corrected
        # re-upload under the same ID) must not leave the previous generation's
        # chunks behind. Stale chunks are worse than missing ones — they are
        # citable evidence for text that no longer exists in the document.
        await self._delete_existing(tenant_id, document_id)

        points = self._build_points(
            chunks=chunks,
            dense_vectors=dense.vectors,
            sparse_pairs=sparse_pairs,
            document_id=document_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            filename=filename,
            role=role,
        )
        await self._upsert(points)

        await self._set_status(document_id, DocumentStatus.READY)
        return IngestionResult(
            document_id=document_id,
            status=DocumentStatus.READY,
            chunk_count=len(chunks),
            page_count=parsed.page_count,
            language=parsed.language,
            text_extraction_ratio=parsed.text_extraction_ratio,
            parse_strategy=parsed.strategy.value,
            table_count=parsed.table_count,
            embedding_tokens=dense.total_tokens or 0,
            warnings=parsed.warnings or None,
        )

    def _build_points(
        self,
        *,
        chunks: list[Chunk],
        dense_vectors: list[list[float]],
        sparse_pairs: list[tuple[list[int], list[float]]],
        document_id: uuid.UUID,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None,
        filename: str,
        role: DocumentRole,
    ) -> list[models.PointStruct]:
        """Assemble Qdrant points.

        Point IDs are deterministic (UUID5 over document ID + chunk index), so
        re-ingesting the same document overwrites rather than duplicates even if
        the delete step above were to fail.
        """
        indexed_at = datetime.now(UTC).isoformat()
        points: list[models.PointStruct] = []

        for chunk, vector, (indices, values) in zip(
            chunks, dense_vectors, sparse_pairs, strict=True
        ):
            payload = ChunkPayload(
                tenant_id=str(tenant_id),
                workspace_id=str(workspace_id) if workspace_id else None,
                document_id=str(document_id),
                document_name=filename,
                document_role=role,
                page_number=chunk.page_number,
                chunk_type=chunk.chunk_type,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                raw_text=chunk.raw_text,
                language=chunk.language,
                section_path=chunk.section_path,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                bbox=chunk.bbox,
                indexed_at=indexed_at,
            )
            points.append(
                models.PointStruct(
                    id=str(uuid.uuid5(document_id, str(chunk.chunk_index))),
                    vector={
                        VectorName.DENSE: vector,
                        VectorName.SPARSE: models.SparseVector(indices=indices, values=values),
                    },
                    payload=payload.to_qdrant(),
                )
            )
        return points

    async def _upsert(self, points: list[models.PointStruct], batch_size: int = 128) -> None:
        """Upsert in batches. A single request with thousands of points from a
        400-page tender exceeds the gRPC message limit.
        """
        try:
            for start in range(0, len(points), batch_size):
                await self._qdrant.upsert(
                    collection_name=self._settings.qdrant_collection,
                    points=points[start : start + batch_size],
                    wait=True,
                )
        except Exception as exc:
            raise VectorStoreError(f"failed to index vectors: {exc}") from exc

    async def _delete_existing(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        from app.rag.vectorstore.collections import QdrantCollectionManager

        try:
            manager = QdrantCollectionManager(self._qdrant, self._settings)
            await manager.delete_document_data(str(tenant_id), str(document_id))
        except Exception as exc:
            raise VectorStoreError(f"failed to clear previous chunks: {exc}") from exc

    async def _set_status(self, document_id: uuid.UUID, status: DocumentStatus) -> None:
        if self._status_callback is not None:
            await self._status_callback(document_id, status)
