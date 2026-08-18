"""Answer generation endpoint.

Synchronous, unlike ingestion: a single grounded answer takes seconds, and a
bid manager working through a compliance matrix needs the result in front of
them. Batch generation across a whole tender goes to Celery instead.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Request, status

from app.api.v1.deps.auth import CurrentUser, get_current_user
from app.core.config import Settings, get_settings
from app.core.exceptions import PermissionDeniedError
from app.db.models.enums import Language
from app.rag.chains.answer_requirement import AnswerChain
from app.schemas.base import APIModel
from app.schemas.proposal import Citation, GenerationRequest, GroundingMetrics
from app.services.generation_service import GenerationService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["generation"])


class GeneratedAnswer(APIModel):
    """Response body for a single generation.

    Deliberately not ``ProposalRead``: nothing is persisted at this point, so
    the row-level fields (id, version, reviewer, timestamps) do not exist yet.
    Returning a half-populated ProposalRead would invite the frontend to treat
    an unsaved draft as a stored one and, worse, to show a reviewer an
    approval control for a row that cannot be approved.
    """

    requirement_ref: str
    requirement_text: str
    answer_text: str | None
    language: Language
    status: str
    citations: list[Citation]
    grounding: GroundingMetrics
    abstention_reason: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    generation_ms: int | None = None

    #: Set only when the answer was saved, which requires a workspace to save
    #: it to. Without these the frontend has no handle to review or approve
    #: with, so their absence is how it knows the result is ephemeral.
    proposal_id: uuid.UUID | None = None
    version: int | None = None


@router.post(
    "/generate-answer",
    response_model=GeneratedAnswer,
    status_code=status.HTTP_200_OK,
    summary="Generate a grounded, cited answer to one tender requirement",
)
async def generate_answer(
    payload: GenerationRequest,
    request: Request,
    workspace_id: uuid.UUID | None = None,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> GeneratedAnswer:
    """Answer one requirement from the caller's own corpus.

    Two outcomes, and both are successes:

    * an answer with citations, always in DRAFT — approval requires a human;
    * an abstention with a stated reason and no answer text, when the evidence
      does not support one.

    With a ``workspace_id`` the result is persisted as a new version and can be
    reviewed; without one it is a scratch query against the tenant's knowledge
    base and nothing is stored. Neither path can produce an APPROVED answer:
    the database CHECK constraint requires a named reviewer, so an accidental
    auto-approval fails loudly instead of silently shipping unreviewed text.

    ``tenant_id`` comes from the bearer token. It is not accepted from the body,
    the query string, or a header — see ``app.api.v1.deps.auth``.
    """
    if user.role.value == "viewer":
        raise PermissionDeniedError("your role cannot generate answers")

    # Held on app.state and shared for the process lifetime; a per-request
    # client would open a new connection pool on every question.
    qdrant = request.app.state.qdrant
    anthropic = getattr(request.app.state, "anthropic", None)
    language = payload.language or Language(settings.default_locale)

    proposal_id: uuid.UUID | None = None
    version: int | None = None

    if workspace_id is None:
        chain = AnswerChain(qdrant, anthropic, settings)
        result = await chain.run(
            requirement_ref=payload.requirement_ref,
            requirement_text=payload.requirement_text,
            tenant_id=user.tenant_id,
            workspace_id=None,
            language=language,
            style_hint=payload.style_hint,
        )
    else:
        result, proposal = await GenerationService(qdrant, anthropic, settings).answer_and_save(
            tenant_id=user.tenant_id,
            workspace_id=workspace_id,
            requirement_ref=payload.requirement_ref,
            requirement_text=payload.requirement_text,
            section_path=payload.section_path,
            is_mandatory=payload.is_mandatory,
            language=language,
            style_hint=payload.style_hint,
        )
        proposal_id = proposal.id
        version = proposal.version

    logger.info(
        "generated %s for tenant %s: status=%s verdict=%s citations=%d",
        result.requirement_ref,
        user.tenant_id,
        result.status.value,
        result.grounding_verdict.value,
        len(result.citations),
    )

    return GeneratedAnswer(
        requirement_ref=result.requirement_ref,
        requirement_text=result.requirement_text,
        answer_text=result.answer_text,
        language=result.language,
        status=result.status.value,
        citations=result.citations,
        grounding=GroundingMetrics(
            verdict=result.grounding_verdict,
            citation_coverage=result.citation_coverage,
            top_retrieval_score=result.top_retrieval_score,
            confidence_score=result.confidence_score,
            citation_count=len(result.citations),
            unsupported_sentences=result.unsupported_sentences,
        ),
        abstention_reason=result.abstention_reason,
        model_id=result.model_id,
        prompt_version=result.prompt_version,
        generation_ms=result.generation_ms,
        proposal_id=proposal_id,
        version=version,
    )
