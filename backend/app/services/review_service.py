"""Human-in-the-loop review: the gate the product's core promise rests on.

Everything upstream of this module is advisory. Retrieval scores, citation
coverage, and the grounding verifier decide how much help a reviewer gets;
none of them decide what ships. A named human does, and this is where that
decision is recorded and constrained.

Three rules are enforced here and nowhere else:

* **Only listed transitions happen.** The status column is a state machine,
  not a free-text field, so no client can move an answer somewhere the
  workflow does not allow.
* **Stale decisions are refused, never merged.** A reviewer's browser holds a
  snapshot; applying a decision made against old text would attach a human's
  sign-off to words they never read.
* **Approval requires evidence.** An answer with no resolvable citation cannot
  be approved as grounded. A reviewer may still take personal responsibility
  for one — but only explicitly, in writing, and it is recorded as such.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    InvalidTransitionError,
    PermissionDeniedError,
    TenantMismatchError,
    UngroundedApprovalError,
    VersionConflictError,
)
from app.db.models.enums import ProposalStatus, UserRole
from app.db.models.proposal import GeneratedProposal
from app.db.repositories.proposals import ProposalRepository
from app.db.session import tenant_session

logger = logging.getLogger(__name__)

#: Legal moves in the review state machine, keyed by the current status.
#:
#: ``EXPORTED`` is deliberately absent: once an answer has gone out in a
#: submission, the submitted text is a historical fact. Changing it produces a
#: new version through regeneration or an edit, which supersedes the exported
#: row rather than rewriting it.
#:
#: ``REJECTED`` cannot jump straight to ``APPROVED`` either. Rejection means the
#: content has to change, and changed content re-enters review through the edit
#: path — approving in place would let a reviewer undo a colleague's rejection
#: without the text moving at all.
TRANSITIONS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.DRAFT: frozenset(
        {
            ProposalStatus.PENDING_REVIEW,
            ProposalStatus.NEEDS_SME,
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
        }
    ),
    ProposalStatus.ABSTAINED: frozenset(
        {
            ProposalStatus.PENDING_REVIEW,
            ProposalStatus.NEEDS_SME,
            ProposalStatus.REJECTED,
        }
    ),
    ProposalStatus.PENDING_REVIEW: frozenset(
        {
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
            ProposalStatus.NEEDS_SME,
        }
    ),
    ProposalStatus.NEEDS_SME: frozenset(
        {
            ProposalStatus.PENDING_REVIEW,
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
        }
    ),
    ProposalStatus.REJECTED: frozenset(
        {
            ProposalStatus.PENDING_REVIEW,
            ProposalStatus.NEEDS_SME,
        }
    ),
    ProposalStatus.APPROVED: frozenset(
        {
            ProposalStatus.REJECTED,
            ProposalStatus.PENDING_REVIEW,
            ProposalStatus.NEEDS_SME,
        }
    ),
    ProposalStatus.EXPORTED: frozenset(),
}

#: Statuses whose text a reviewer may still edit. An exported answer is
#: excluded for the same reason it cannot be re-reviewed.
EDITABLE = frozenset(
    {
        ProposalStatus.DRAFT,
        ProposalStatus.ABSTAINED,
        ProposalStatus.PENDING_REVIEW,
        ProposalStatus.NEEDS_SME,
        ProposalStatus.REJECTED,
        ProposalStatus.APPROVED,
    }
)


@dataclass(slots=True)
class ReviewProgress:
    """Export readiness for one workspace."""

    approved: int
    total: int
    #: Mandatory answers still lacking a human sign-off. The export gate reads
    #: this, and nothing else.
    outstanding: int
    ready_to_export: bool


class ReviewService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------ reads
    async def get(self, *, tenant_id: uuid.UUID, proposal_id: uuid.UUID) -> GeneratedProposal:
        async with tenant_session(tenant_id, self._settings) as session:
            proposal = await ProposalRepository(session).get_by_id(proposal_id)
            if proposal is None:
                raise TenantMismatchError("proposal not found")
            return proposal

    async def queue(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        status: ProposalStatus | None = None,
        assigned_sme_id: uuid.UUID | None = None,
        mandatory_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[GeneratedProposal], int]:
        async with tenant_session(tenant_id, self._settings) as session:
            return await ProposalRepository(session).list_for_review(
                workspace_id=workspace_id,
                status=status,
                assigned_sme_id=assigned_sme_id,
                mandatory_only=mandatory_only,
                limit=limit,
                offset=offset,
            )

    async def progress(self, *, tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> ReviewProgress:
        """Export readiness over *mandatory* requirements only.

        Counting optional items here would block a submission that is in fact
        complete, which trains reviewers to look for a way around the gate —
        the one outcome a fail-closed control cannot survive.
        """
        async with tenant_session(tenant_id, self._settings) as session:
            approved, total = await ProposalRepository(session).count_approved(workspace_id)
        outstanding = total - approved
        return ReviewProgress(
            approved=approved,
            total=total,
            outstanding=outstanding,
            # An empty workspace is not ready: nothing has been answered, so
            # there is nothing anyone has signed off on.
            ready_to_export=total > 0 and outstanding == 0,
        )

    # ----------------------------------------------------------------- writes
    async def apply_action(
        self,
        *,
        tenant_id: uuid.UUID,
        proposal_id: uuid.UUID,
        actor_id: uuid.UUID,
        actor_role: UserRole,
        action: ProposalStatus,
        expected_version: int,
        review_notes: str | None = None,
        assigned_sme_id: uuid.UUID | None = None,
        acknowledge_ungrounded: bool = False,
    ) -> GeneratedProposal:
        """Move one answer through the review state machine.

        Raises:
            TenantMismatchError: no such row for this tenant (404, not 403 —
                a 403 would confirm the id exists in someone else's tenant).
            VersionConflictError: the row moved since the reviewer loaded it.
            InvalidTransitionError: the move is not legal from this state.
            PermissionDeniedError: the actor's role cannot make this decision.
            UngroundedApprovalError: approval attempted on an answer with no
                citations and no explicit acknowledgement.
        """
        async with tenant_session(tenant_id, self._settings) as session:
            repository = ProposalRepository(session)
            proposal = await repository.get_by_id(proposal_id)
            if proposal is None:
                raise TenantMismatchError("proposal not found")

            _require_fresh(proposal, expected_version)
            _require_legal_transition(proposal.status, action)
            _require_role(actor_role, action, proposal, actor_id)

            if action is ProposalStatus.APPROVED:
                _require_evidence(proposal, acknowledge_ungrounded, review_notes)

            # Captured before the write, or the log line reports the new state
            # on both sides of the arrow and the audit trail loses the move.
            previous_status = proposal.status
            await repository.touch_reviewed(
                proposal,
                reviewer_id=actor_id,
                status=action,
                notes=review_notes,
                assigned_sme_id=assigned_sme_id,
            )
            logger.info(
                "review: proposal=%s %s -> %s by %s (v%d)",
                proposal.id,
                previous_status.value,
                action.value,
                actor_id,
                proposal.version,
            )
            return proposal

    async def apply_edit(
        self,
        *,
        tenant_id: uuid.UUID,
        proposal_id: uuid.UUID,
        actor_id: uuid.UUID,
        actor_role: UserRole,
        edited_text: str,
        expected_version: int,
        review_notes: str | None = None,
    ) -> GeneratedProposal:
        """Store a reviewer's rewrite.

        Editing an approved answer revokes the approval. The alternative —
        letting the sign-off ride along with the new text — would mean the
        record shows a named human approving words that did not exist when
        they clicked approve.
        """
        async with tenant_session(tenant_id, self._settings) as session:
            repository = ProposalRepository(session)
            proposal = await repository.get_by_id(proposal_id)
            if proposal is None:
                raise TenantMismatchError("proposal not found")

            _require_fresh(proposal, expected_version)
            if proposal.status not in EDITABLE:
                raise InvalidTransitionError(
                    f"an answer in state {proposal.status.value!r} cannot be edited",
                    current=proposal.status.value,
                )
            if actor_role is UserRole.VIEWER:
                raise PermissionDeniedError("your role cannot edit answers")
            if actor_role is UserRole.SME and proposal.assigned_sme_id != actor_id:
                raise PermissionDeniedError("this answer is not assigned to you")

            reset = (
                ProposalStatus.PENDING_REVIEW
                if proposal.status is ProposalStatus.APPROVED
                else None
            )
            await repository.apply_edit(
                proposal,
                edited_text=edited_text,
                notes=review_notes,
                reset_status=reset,
            )
            if reset is not None:
                logger.info("edit revoked approval on proposal %s", proposal.id)
            return proposal


# ------------------------------------------------------------------ guards
def _require_fresh(proposal: GeneratedProposal, expected_version: int) -> None:
    if proposal.version != expected_version:
        raise VersionConflictError(
            "the answer changed since it was loaded",
            expected_version=expected_version,
            current_version=proposal.version,
        )


def _require_legal_transition(current: ProposalStatus, action: ProposalStatus) -> None:
    if action not in TRANSITIONS.get(current, frozenset()):
        raise InvalidTransitionError(
            f"cannot move an answer from {current.value!r} to {action.value!r}",
            current=current.value,
            requested=action.value,
            allowed=sorted(s.value for s in TRANSITIONS.get(current, frozenset())),
        )


def _require_role(
    actor_role: UserRole,
    action: ProposalStatus,
    proposal: GeneratedProposal,
    actor_id: uuid.UUID,
) -> None:
    """Approval and rejection are a bid manager's call, not an SME's.

    An SME answers the items assigned to them and hands them back for review;
    letting them sign off their own work would remove the second pair of eyes
    that the whole gate exists to provide.
    """
    if actor_role is UserRole.VIEWER:
        raise PermissionDeniedError("your role cannot review answers")

    if actor_role is UserRole.SME:
        if action in (ProposalStatus.APPROVED, ProposalStatus.REJECTED):
            raise PermissionDeniedError("only a bid manager or owner can approve or reject")
        if proposal.assigned_sme_id != actor_id:
            raise PermissionDeniedError("this answer is not assigned to you")


def _require_evidence(
    proposal: GeneratedProposal,
    acknowledge_ungrounded: bool,
    review_notes: str | None,
) -> None:
    """The golden rule, enforced at the last moment it still can be.

    Nothing may be approved without a citation pointing at a real source in the
    customer's own documents. The single exception is a reviewer writing the
    answer themselves — which is legitimate, common, and must be visible in the
    audit trail rather than indistinguishable from a grounded answer. So it
    costs an explicit flag *and* a written note, and the default refuses.
    """
    text = proposal.edited_text or proposal.answer_text
    if not text or not text.strip():
        raise UngroundedApprovalError(
            "there is no answer text to approve",
            reason="empty",
        )
    if proposal.citations:
        return
    if not acknowledge_ungrounded:
        raise UngroundedApprovalError(
            "this answer carries no citations; approving it requires an explicit "
            "acknowledgement that you are vouching for it yourself",
            reason="no_citations",
        )
    if not (review_notes and review_notes.strip()):
        raise UngroundedApprovalError(
            "approving an uncited answer requires review notes recording why",
            reason="acknowledgement_without_notes",
        )
    logger.warning(
        "proposal %s approved without citations under explicit acknowledgement",
        proposal.id,
    )


__all__ = ["EDITABLE", "TRANSITIONS", "ReviewProgress", "ReviewService"]
