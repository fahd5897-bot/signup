"""Review workbench endpoints — the human-in-the-loop gate.

Nothing in this product may be exported without passing through here. The
router is deliberately thin: every rule that decides whether a decision is
allowed lives in :mod:`app.services.review_service`, so the same guarantees
hold for the Celery batch path and any future CLI, not just for HTTP callers.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, status

from app.api.v1.deps.auth import CurrentUser, get_current_user
from app.core.config import Settings, get_settings
from app.db.models.enums import ProposalStatus
from app.db.models.proposal import GeneratedProposal
from app.schemas.base import APIModel
from app.schemas.common import Page
from app.schemas.proposal import (
    ProposalEdit,
    ProposalRead,
    ProposalReviewAction,
    ProposalSummary,
)
from app.services.review_service import ReviewService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["review"])


class ReviewProgressRead(APIModel):
    """Export readiness for a workspace.

    ``ready_to_export`` is the single value the export endpoint consults. It is
    computed over mandatory requirements only, and an empty workspace is never
    ready — nothing answered means nothing signed off.
    """

    approved: int
    total: int
    outstanding: int
    ready_to_export: bool


def _summarise(proposal: GeneratedProposal) -> ProposalSummary:
    return ProposalSummary(
        id=proposal.id,
        requirement_ref=proposal.requirement_ref,
        is_mandatory=proposal.is_mandatory,
        status=proposal.status,
        grounding_verdict=proposal.grounding_verdict,
        confidence_score=proposal.confidence_score,
        citation_count=len(proposal.citations or []),
        version=proposal.version,
    )


@router.get(
    "/workspaces/{workspace_id}/review-queue",
    response_model=Page[ProposalSummary],
    summary="List answers awaiting review",
)
async def review_queue(
    workspace_id: uuid.UUID,
    status_filter: ProposalStatus | None = None,
    assigned_to_me: bool = False,
    mandatory_only: bool = False,
    limit: int = 100,
    offset: int = 0,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> Page[ProposalSummary]:
    """Summaries only.

    Answer bodies and citation arrays stay out of a 200-row list response —
    a full compliance matrix would otherwise be megabytes of JSON before the
    reviewer has opened anything.
    """
    proposals, total = await ReviewService(settings).queue(
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        status=status_filter,
        assigned_sme_id=user.id if assigned_to_me else None,
        mandatory_only=mandatory_only,
        limit=min(limit, 200),
        offset=offset,
    )
    return Page[ProposalSummary](
        items=[_summarise(p) for p in proposals],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/workspaces/{workspace_id}/review-progress",
    response_model=ReviewProgressRead,
    summary="How much of this tender is signed off",
)
async def review_progress(
    workspace_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ReviewProgressRead:
    progress = await ReviewService(settings).progress(
        tenant_id=user.tenant_id, workspace_id=workspace_id
    )
    return ReviewProgressRead(
        approved=progress.approved,
        total=progress.total,
        outstanding=progress.outstanding,
        ready_to_export=progress.ready_to_export,
    )


@router.get(
    "/proposals/{proposal_id}",
    response_model=ProposalRead,
    summary="Open one answer with its full evidence",
)
async def get_proposal(
    proposal_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ProposalRead:
    """A row from another tenant returns 404, not 403.

    Under RLS it is genuinely invisible to this session; answering 403 would
    confirm the id exists and turn the endpoint into an enumeration oracle.
    """
    proposal = await ReviewService(settings).get(tenant_id=user.tenant_id, proposal_id=proposal_id)
    return ProposalRead.model_validate(proposal)


@router.post(
    "/proposals/{proposal_id}/review",
    response_model=ProposalRead,
    status_code=status.HTTP_200_OK,
    summary="Approve, reject, or escalate an answer",
)
async def review_proposal(
    proposal_id: uuid.UUID,
    payload: ProposalReviewAction,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ProposalRead:
    """Record a human decision.

    ``expected_version`` is mandatory. A reviewer's browser holds a snapshot,
    and applying a decision made against superseded text would attach a named
    human's approval to words they never read — so a stale version is refused
    with 409 rather than merged.
    """
    proposal = await ReviewService(settings).apply_action(
        tenant_id=user.tenant_id,
        proposal_id=proposal_id,
        actor_id=user.id,
        actor_role=user.role,
        action=payload.action,
        expected_version=payload.expected_version,
        review_notes=payload.review_notes,
        assigned_sme_id=payload.assigned_sme_id,
        acknowledge_ungrounded=payload.acknowledge_ungrounded,
    )
    return ProposalRead.model_validate(proposal)


@router.patch(
    "/proposals/{proposal_id}",
    response_model=ProposalRead,
    summary="Edit an answer's text",
)
async def edit_proposal(
    proposal_id: uuid.UUID,
    payload: ProposalEdit,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ProposalRead:
    """Store the reviewer's wording alongside the generated original.

    Editing an approved answer sends it back to review: the sign-off belonged
    to the previous text.
    """
    proposal = await ReviewService(settings).apply_edit(
        tenant_id=user.tenant_id,
        proposal_id=proposal_id,
        actor_id=user.id,
        actor_role=user.role,
        edited_text=payload.edited_text,
        expected_version=payload.expected_version,
        review_notes=payload.review_notes,
    )
    return ProposalRead.model_validate(proposal)
