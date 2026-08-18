"""Build a workspace's compliance matrix from an ingested tender document.

Extraction produces the work list; this module makes it durable. Each
requirement becomes a proposal row with no answer yet — DRAFT, ``answer_text``
NULL, no citations — which is exactly what the review gate refuses to approve
and what the export gate counts as outstanding. So a freshly extracted tender
is, correctly, 0% ready to submit rather than silently exportable.

The chunks are read back out of Qdrant rather than re-parsed from object
storage. Re-parsing a 400-page scanned Arabic tender costs minutes of OCR to
recover text the ingestion pipeline already extracted, and would risk the two
copies disagreeing.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, TenantMismatchError
from app.db.models.enums import DocumentRole, DocumentStatus, GroundingVerdict, ProposalStatus
from app.db.repositories.documents import DocumentRepository
from app.db.repositories.proposals import ProposalRepository
from app.db.session import tenant_session
from app.rag.chains.extract_requirements import (
    ExtractionResult,
    RequirementExtractionChain,
    SourceChunk,
)
from app.rag.vectorstore.filters import build_search_filter
from app.rag.vectorstore.schema import PayloadField

logger = logging.getLogger(__name__)

#: Points fetched per scroll page. A large tender is a few thousand chunks.
_SCROLL_BATCH = 256

#: Hard ceiling on one extraction run, so a mis-ingested document cannot spend
#: an unbounded number of model calls.
_MAX_CHUNKS = 4_000


class DocumentNotReadyError(AppError):
    """Extraction was asked for before ingestion finished.

    Running anyway would produce a partial matrix that looks complete — the
    worst possible artefact, because the missing clauses are invisible.
    """

    slug = "document_not_ready"
    status_code = 409
    user_message = "This document is still being processed."


class NotATenderError(AppError):
    """Requirements come from the tender, not from the knowledge base.

    Extracting from a knowledge-base document would fill the matrix with the
    bidder's own marketing copy restated as obligations.
    """

    slug = "not_a_tender_document"
    status_code = 422
    user_message = "Requirements can only be extracted from a tender document."


@dataclass(slots=True)
class RegisteredRequirements:
    workspace_id: uuid.UUID
    document_id: uuid.UUID
    created: int
    skipped_existing: int
    extraction: ExtractionResult

    @property
    def mandatory(self) -> int:
        return self.extraction.mandatory_count


class RequirementService:
    def __init__(
        self,
        qdrant: AsyncQdrantClient,
        anthropic=None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._qdrant = qdrant
        self._chain = RequirementExtractionChain(anthropic, self._settings)

    async def extract_and_register(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID,
        overwrite_existing: bool = False,
    ) -> RegisteredRequirements:
        """Read the tender, then write one unanswered proposal per requirement.

        Raises:
            TenantMismatchError: no such document for this tenant.
            DocumentNotReadyError: ingestion has not finished.
            NotATenderError: the document is knowledge base, not tender.
        """
        async with tenant_session(tenant_id, self._settings) as session:
            document = await DocumentRepository(session).get(document_id)
            if document is None:
                raise TenantMismatchError("document not found")
            if document.status is not DocumentStatus.READY:
                raise DocumentNotReadyError(
                    f"document is {document.status.value}, not ready",
                    document_status=document.status.value,
                )
            if document.role is not DocumentRole.TENDER:
                raise NotATenderError(
                    f"document role is {document.role.value}",
                    document_role=document.role.value,
                )

        chunks = await self._load_chunks(tenant_id, document_id)
        if not chunks:
            # Ingestion reported READY but the collection has nothing. That is
            # a broken index, not an empty tender, and answering "0
            # requirements" would read as a clean bill of health.
            raise DocumentNotReadyError(
                "the document is marked ready but no indexed content was found",
                document_status=DocumentStatus.READY.value,
            )

        extraction = await self._chain.run(chunks)

        created = 0
        skipped = 0
        async with tenant_session(tenant_id, self._settings) as session:
            repository = ProposalRepository(session)
            for requirement in extraction.requirements:
                existing = await repository.get_current(
                    workspace_id=workspace_id,
                    requirement_ref=requirement.requirement_ref,
                )
                if existing is not None and not overwrite_existing:
                    # Re-running extraction must never discard a drafted or
                    # approved answer just because the clause was re-read.
                    skipped += 1
                    continue

                await repository.save_generation(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    requirement_ref=requirement.requirement_ref,
                    requirement_text=requirement.requirement_text,
                    section_path=requirement.section_path,
                    is_mandatory=requirement.is_mandatory,
                    # No answer yet. The row exists so the requirement is
                    # visible and counted as outstanding; the generation pass
                    # supersedes it with a cited answer.
                    answer_text=None,
                    language=requirement.language,
                    status=ProposalStatus.DRAFT,
                    citations=[],
                    retrieved_chunk_ids=[],
                    grounding_verdict=GroundingVerdict.NOT_APPLICABLE,
                    citation_coverage=None,
                    top_retrieval_score=None,
                    confidence_score=None,
                    abstention_reason=None,
                    model_id=extraction.model_id,
                    prompt_version=extraction.prompt_version,
                )
                created += 1

        logger.info(
            "registered %d requirements (%d already present) for workspace %s",
            created,
            skipped,
            workspace_id,
        )
        return RegisteredRequirements(
            workspace_id=workspace_id,
            document_id=document_id,
            created=created,
            skipped_existing=skipped,
            extraction=extraction,
        )

    # ------------------------------------------------------------- internals
    async def _load_chunks(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> list[SourceChunk]:
        """Scroll this document's chunks back out of the vector store.

        ``include_tender_documents=True`` is required and is the only place in
        the codebase that sets it: everywhere else a tender document is the
        question, and letting it serve as evidence produces answers that cite
        the customer's own requirement back at them. Here it *is* the subject.
        """
        scroll_filter = build_search_filter(
            tenant_id=tenant_id,
            document_ids=[document_id],
            include_tender_documents=True,
        )

        collected: list[SourceChunk] = []
        offset = None
        while True:
            points, offset = await self._qdrant.scroll(
                collection_name=self._settings.qdrant_collection,
                scroll_filter=scroll_filter,
                limit=_SCROLL_BATCH,
                offset=offset,
                with_payload=True,
                # The vectors are megabytes per page and nothing here reads
                # them; extraction works on text.
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                raw = payload.get(PayloadField.RAW_TEXT) or payload.get(PayloadField.TEXT)
                if not raw:
                    continue
                collected.append(
                    SourceChunk(
                        raw_text=raw,
                        chunk_index=int(payload.get(PayloadField.CHUNK_INDEX) or 0),
                        page_number=payload.get(PayloadField.PAGE_NUMBER),
                        section_path=payload.get(PayloadField.SECTION_PATH),
                    )
                )
            if offset is None or len(collected) >= _MAX_CHUNKS:
                break

        if len(collected) >= _MAX_CHUNKS:
            logger.warning(
                "document %s truncated to %d chunks for extraction", document_id, _MAX_CHUNKS
            )

        # Scroll returns points in id order, which is insertion order at best.
        # Document order is what makes the matrix readable and what the
        # windowing relies on to keep a clause with its heading.
        collected.sort(key=lambda c: c.chunk_index)
        return collected[:_MAX_CHUNKS]


__all__ = [
    "DocumentNotReadyError",
    "NotATenderError",
    "RegisteredRequirements",
    "RequirementService",
]
