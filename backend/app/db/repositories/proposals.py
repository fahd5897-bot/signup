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

    async def get_by_id(self, proposal_id: uuid.UUID) -> GeneratedProposal | None:
        """Load one proposal by primary key.

        No tenant predicate: the session is already scoped by RLS, so a row
        belonging to another tenant simply is not there. Adding a redundant
        filter would create a second place tenant isolation could be wrong.
        """
        return (
            await self._session.execute(
                select(GeneratedProposal).where(
                    GeneratedProposal.id == proposal_id,
                    GeneratedProposal.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def list_for_review(
        self,
        *,
        workspace_id: uuid.UUID,
        status: ProposalStatus | None = None,
        assigned_sme_id: uuid.UUID | None = None,
        mandatory_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[GeneratedProposal], int]:
        """The review queue.

        Ordered by requirement reference rather than by confidence: a reviewer
        working a compliance matrix reads it in the tender's own order, and
        re-sorting by model confidence makes it impossible to tell whether an
        item was skipped.
        """
        conditions = [
            GeneratedProposal.workspace_id == workspace_id,
            GeneratedProposal.is_current.is_(True),
            GeneratedProposal.deleted_at.is_(None),
        ]
        if status is not None:
            conditions.append(GeneratedProposal.status == status)
        if assigned_sme_id is not None:
            conditions.append(GeneratedProposal.assigned_sme_id == assigned_sme_id)
        if mandatory_only:
            conditions.append(GeneratedProposal.is_mandatory.is_(True))

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

    #: States that count as signed off by a human. ``EXPORTED`` is included
    #: because it is reached only by passing the approval gate — it is a
    #: stronger state than ``APPROVED``, not a weaker one. Counting only
    #: ``APPROVED`` would mean the first export locks the workspace out of
    #: every later one, and a submission normally needs both the response
    #: document and the compliance matrix.
    SIGNED_OFF = (ProposalStatus.APPROVED, ProposalStatus.EXPORTED)

    async def count_approved(self, workspace_id: uuid.UUID) -> tuple[int, int]:
        """Return ``(signed off, total)`` over current mandatory requirements.

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
                .where(*base, GeneratedProposal.status.in_(self.SIGNED_OFF))
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
        assigned_sme_id: uuid.UUID | None = None,
    ) -> None:
        """Record a review decision and advance the row revision.

        ``version`` is the optimistic-lock column, and it is incremented here
        rather than by SQLAlchemy because the mapper is configured with
        ``version_id_generator=False``. Without the bump, two reviewers holding
        the same revision would both pass their ``expected_version`` check and
        the second decision would silently overwrite the first.
        """
        proposal.status = status
        proposal.reviewed_by_id = reviewer_id
        proposal.reviewed_at = datetime.now(UTC)
        proposal.version = proposal.version + 1
        if notes is not None:
            proposal.review_notes = notes
        if assigned_sme_id is not None:
            proposal.assigned_sme_id = assigned_sme_id
        await self._session.flush()
        # `updated_at` is computed by PostgreSQL via `onupdate`, so after the
        # UPDATE the in-memory value is stale and SQLAlchemy marks it for
        # re-fetch. The caller reads this row after the session closes, where
        # a re-fetch raises DetachedInstanceError — which surfaces as a 500 at
        # serialisation time, long after the write itself succeeded.
        await self._session.refresh(proposal)

    async def apply_edit(
        self,
        proposal: GeneratedProposal,
        *,
        edited_text: str,
        notes: str | None = None,
        reset_status: ProposalStatus | None = None,
    ) -> None:
        """Store a reviewer's edit without touching the generated original.

        ``answer_text`` is never overwritten: the human-vs-model delta is the
        only honest quality metric this product has, and an in-place edit
        destroys it. When the edit lands on an already-approved answer the
        caller passes ``reset_status``, which clears the sign-off — otherwise a
        reviewer's approval would silently carry over to text they never read.
        """
        proposal.edited_text = edited_text
        proposal.version = proposal.version + 1
        if notes is not None:
            proposal.review_notes = notes
        if reset_status is not None:
            proposal.status = reset_status
            proposal.reviewed_by_id = None
            proposal.reviewed_at = None
        await self._session.flush()
        # See `touch_reviewed`: resolve the server-computed `updated_at` while
        # the session is still open.
        await self._session.refresh(proposal)
