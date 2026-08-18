"""Data access for generated proposals."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.enums import GroundingVerdict, Language, ProposalStatus
from app.db.models.proposal import GeneratedProposal


class ProposalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(
        self, *, workspace_id: uuid.UUID, requirement_ref: str
    ) -> GeneratedProposal | None:
        return (
            await self._session.execute(
                select(GeneratedProposal).where(
                    GeneratedProposal.workspace_id == workspace_id,
                    GeneratedProposal.requirement_ref == requirement_ref,
                    GeneratedProposal.is_current.is_(True),
                    GeneratedProposal.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def save_generation(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        requirement_ref: str,
        requirement_text: str,
        answer_text: str | None,
        language: Language,
        status: ProposalStatus,
        citations: list[dict[str, object]],
        retrieved_chunk_ids: list[str],
        grounding_verdict: GroundingVerdict,
        citation_coverage: float | None,
        top_retrieval_score: float | None,
        confidence_score: float | None,
        abstention_reason: str | None,
        model_id: str | None,
        prompt_version: str | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        generation_ms: int | None = None,
        section_path: str | None = None,
        is_mandatory: bool = True,
        trace_id: str | None = None,
    ) -> GeneratedProposal:
        """Persist a generation as a new immutable version.

        Regenerating supersedes rather than overwrites: the previous row keeps
        its text, citations, and reviewer decision, and only loses ``is_current``.
        A submitted tender response must be reconstructible exactly as it was
        approved, which an in-place UPDATE destroys — and the partial unique
        index means only one version per requirement can be current at a time,
        so the supersede has to happen in the same transaction as the insert.
        """
        previous = await self.get_current(
            workspace_id=workspace_id, requirement_ref=requirement_ref
        )

        version = 1
        previous_version_id = None
        if previous is not None:
            previous.is_current = False
            version = previous.version + 1
            previous_version_id = previous.id
            # Flush before inserting: the partial unique index would otherwise
            # see two current rows for the same requirement within the
            # statement and reject the insert.
            await self._session.flush()

        proposal = GeneratedProposal(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            requirement_ref=requirement_ref,
            requirement_text=requirement_text,
            section_path=section_path,
            is_mandatory=is_mandatory,
            answer_text=answer_text,
            language=language,
            citations=citations,
            retrieved_chunk_ids=retrieved_chunk_ids,
            grounding_verdict=grounding_verdict,
            citation_coverage=citation_coverage,
            top_retrieval_score=top_retrieval_score,
            confidence_score=confidence_score,
            abstention_reason=abstention_reason,
            # Always DRAFT or ABSTAINED. Nothing on the generation path may
            # write APPROVED; the CHECK constraint requires a named reviewer.
            status=status,
            version=version,
            is_current=True,
            previous_version_id=previous_version_id,
            model_id=model_id,
            prompt_version=prompt_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            generation_ms=generation_ms,
            trace_id=trace_id,
        )
        self._session.add(proposal)
        await self._session.flush()
        return proposal

    async def list_current(
        self, *, workspace_id: uuid.UUID, limit: int = 200, offset: int = 0
    ) -> tuple[list[GeneratedProposal], int]:
        conditions = (
            GeneratedProposal.workspace_id == workspace_id,
            GeneratedProposal.is_current.is_(True),
            GeneratedProposal.deleted_at.is_(None),
        )
        total = (
            await self._session.execute(
                select(func.count()).select_from(GeneratedProposal).where(*conditions)
            )
        ).scalar_one()
        rows = (
            (
                await self._session.execute(
                    select(GeneratedProposal)
                    .where(*conditions)
                    .order_by(GeneratedProposal.requirement_ref)
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return list(rows), total

    async def history(
        self, *, workspace_id: uuid.UUID, requirement_ref: str
    ) -> list[GeneratedProposal]:
        """Every version of one answer, newest first — the audit view."""
        return list(
            (
                await self._session.execute(
                    select(GeneratedProposal)
                    .where(
                        GeneratedProposal.workspace_id == workspace_id,
                        GeneratedProposal.requirement_ref == requirement_ref,
                    )
                    .order_by(GeneratedProposal.version.desc())
                )
            )
            .scalars()
            .all()
        )

    async def count_approved(self, workspace_id: uuid.UUID) -> tuple[int, int]:
        """Return ``(approved, total)`` over current mandatory requirements.

        Mandatory only: the export gate turns on whether every *required* item
        is signed off, and counting optional ones would block a submission that
        is actually complete.
        """
        base = (
            GeneratedProposal.workspace_id == workspace_id,
            GeneratedProposal.is_current.is_(True),
            GeneratedProposal.deleted_at.is_(None),
            GeneratedProposal.is_mandatory.is_(True),
        )
        total = (
            await self._session.execute(
                select(func.count()).select_from(GeneratedProposal).where(*base)
            )
        ).scalar_one()
        approved = (
            await self._session.execute(
                select(func.count())
                .select_from(GeneratedProposal)
                .where(*base, GeneratedProposal.status == ProposalStatus.APPROVED)
            )
        ).scalar_one()
        return approved, total

    async def touch_reviewed(
        self,
        proposal: GeneratedProposal,
        *,
        reviewer_id: uuid.UUID,
        status: ProposalStatus,
        notes: str | None = None,
    ) -> None:
        proposal.status = status
        proposal.reviewed_by_id = reviewer_id
        proposal.reviewed_at = datetime.now(UTC)
        if notes is not None:
            proposal.review_notes = notes
