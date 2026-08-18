"""Generation with persistence.

Wraps :class:`app.rag.chains.answer_requirement.AnswerChain` so a generated
answer becomes a durable, versioned row instead of a value that disappears on
the next page load. The chain itself stays free of any database dependency,
which is what lets it be tested against a live Qdrant without a PostgreSQL.
"""

from __future__ import annotations

import logging
import uuid

from qdrant_client import AsyncQdrantClient

from app.core.config import Settings, get_settings
from app.db.models.enums import Language, ProposalStatus
from app.db.models.proposal import GeneratedProposal
from app.db.repositories.proposals import ProposalRepository
from app.db.session import tenant_session
from app.rag.chains.answer_requirement import AnswerChain, AnswerResult

logger = logging.getLogger(__name__)


class GenerationService:
    def __init__(
        self,
        qdrant: AsyncQdrantClient,
        anthropic=None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._chain = AnswerChain(qdrant, anthropic, self._settings)

    async def answer_and_save(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        requirement_ref: str,
        requirement_text: str,
        section_path: str | None = None,
        is_mandatory: bool = True,
        language: Language = Language.EN,
        style_hint: str | None = None,
    ) -> tuple[AnswerResult, GeneratedProposal]:
        """Generate an answer and persist it as the current version.

        Generation runs *outside* the database transaction. It takes seconds and
        makes network calls; holding a PostgreSQL transaction open across that
        would pin a pooled connection for the duration and, under load, starve
        the pool while every request waits on someone else's model call.
        """
        result = await self._chain.run(
            requirement_ref=requirement_ref,
            requirement_text=requirement_text,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            language=language,
            style_hint=style_hint,
        )

        async with tenant_session(tenant_id, self._settings) as session:
            proposal = await ProposalRepository(session).save_generation(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                requirement_ref=result.requirement_ref,
                requirement_text=result.requirement_text,
                section_path=section_path,
                is_mandatory=is_mandatory,
                answer_text=result.answer_text,
                language=result.language,
                # DRAFT or ABSTAINED only. The CHECK constraint refuses
                # APPROVED without a named reviewer, so an accidental
                # auto-approval fails loudly at the database rather than
                # silently shipping an unreviewed answer.
                status=result.status,
                citations=[c.model_dump(mode="json") for c in result.citations],
                retrieved_chunk_ids=result.retrieved_chunk_ids,
                grounding_verdict=result.grounding_verdict,
                citation_coverage=result.citation_coverage,
                top_retrieval_score=result.top_retrieval_score,
                confidence_score=result.confidence_score,
                abstention_reason=result.abstention_reason,
                model_id=result.model_id,
                prompt_version=result.prompt_version,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                generation_ms=result.generation_ms,
            )

        logger.info(
            "saved proposal %s v%d for %s (%s)",
            proposal.id,
            proposal.version,
            requirement_ref,
            result.status.value,
        )
        return result, proposal

    async def list_for_workspace(
        self, *, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> tuple[list[GeneratedProposal], int]:
        async with tenant_session(tenant_id, self._settings) as session:
            return await ProposalRepository(session).list_current(workspace_id=workspace_id)

    async def progress(self, *, tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> tuple[int, int]:
        """``(approved, total)`` over mandatory requirements — the export gate."""
        async with tenant_session(tenant_id, self._settings) as session:
            return await ProposalRepository(session).count_approved(workspace_id)

    async def history(
        self, *, tenant_id: uuid.UUID, workspace_id: uuid.UUID, requirement_ref: str
    ) -> list[GeneratedProposal]:
        async with tenant_session(tenant_id, self._settings) as session:
            return await ProposalRepository(session).history(
                workspace_id=workspace_id, requirement_ref=requirement_ref
            )


__all__ = ["GenerationService", "ProposalStatus"]
