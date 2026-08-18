"""Requirement extraction — where a tender becomes a work list.

Synchronous like generation, and for the same reason: a bid manager who has
just uploaded a tender is sitting in front of the screen waiting to see what it
asks for. A very large document is windowed internally rather than handed to
Celery, so the request returns with a matrix rather than a task id to poll.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Request, status

from app.api.v1.deps.auth import CurrentUser, get_current_user
from app.core.config import Settings, get_settings
from app.core.exceptions import PermissionDeniedError
from app.db.models.enums import UserRole
from app.schemas.proposal import (
    ExtractedRequirementRead,
    RequirementExtractionRequest,
    RequirementExtractionResult,
)
from app.services.requirement_service import RequirementService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["requirements"])


@router.post(
    "/workspaces/{workspace_id}/extract-requirements",
    response_model=RequirementExtractionResult,
    status_code=status.HTTP_201_CREATED,
    summary="Read a tender document into a compliance matrix",
)
async def extract_requirements(
    workspace_id: uuid.UUID,
    payload: RequirementExtractionRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> RequirementExtractionResult:
    """Extract every requirement in the tender and register it as outstanding.

    Each requirement becomes a proposal row with no answer, which is what the
    export gate counts as outstanding — so the workspace correctly reads as 0%
    ready immediately after extraction rather than as exportable.

    Restricted to owners and bid managers: the matrix defines the shape of the
    whole submission, and re-running it with ``overwrite_existing`` discards
    drafted work.
    """
    if user.role not in (UserRole.OWNER, UserRole.BID_MANAGER):
        raise PermissionDeniedError("your role cannot define the compliance matrix")

    qdrant = request.app.state.qdrant
    anthropic = getattr(request.app.state, "anthropic", None)

    registered = await RequirementService(qdrant, anthropic, settings).extract_and_register(
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        document_id=payload.document_id,
        overwrite_existing=payload.overwrite_existing,
    )

    extraction = registered.extraction
    if extraction.drop_ratio > 0.25:
        # Worth a log line at warning level: a quarter of proposed requirements
        # failing to resolve against the document usually means the source is
        # badly OCR'd, and the resulting matrix is not trustworthy.
        logger.warning(
            "extraction for document %s dropped %.0f%% of proposed requirements",
            payload.document_id,
            extraction.drop_ratio * 100,
        )

    return RequirementExtractionResult(
        document_id=registered.document_id,
        workspace_id=registered.workspace_id,
        created=registered.created,
        skipped_existing=registered.skipped_existing,
        mandatory=registered.mandatory,
        dropped=len(extraction.dropped),
        windows=extraction.windows,
        requirements=[
            ExtractedRequirementRead(
                requirement_ref=r.requirement_ref,
                requirement_text=r.requirement_text,
                source_text=r.source_text,
                is_mandatory=r.is_mandatory,
                category=r.category,
                section_path=r.section_path,
                page_number=r.page_number,
                ref_is_synthetic=r.ref_is_synthetic,
            )
            for r in extraction.requirements
        ],
    )
