"""GeneratedProposal schemas — the answer, its evidence, and its review.

:class:`Citation` is the important one: it is the write-time contract for the
``generated_proposals.citations`` JSONB column, and the only thing standing
between "the database has a JSON blob" and "every stored citation is
structurally valid and resolvable".
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, computed_field, model_validator

from app.db.models.enums import ChunkType, GroundingVerdict, Language, ProposalStatus
from app.schemas.base import APIModel, StrictModel, TimestampedRead


class Citation(APIModel):
    """One evidence span backing a claim in a generated answer.

    Character offsets and ``quoted_text`` come from the Anthropic citations API,
    which computes them server-side against the exact document bytes supplied in
    the request. That provenance is the whole point: the model is not asked to
    report where it found something (which it would happily invent) — the API
    reports it. A citation that cannot be resolved back to a real chunk is
    therefore detectable, and :mod:`app.rag.grounding.verifier` rejects it.

    The payload fields mirror the Qdrant point payload one-for-one, so a
    citation can be resolved to its source chunk without a database round trip.
    """

    #: Qdrant point ID of the chunk this span was drawn from.
    chunk_id: str
    document_id: uuid.UUID
    document_name: str
    #: 1-indexed, matching what a reader sees in a PDF viewer.
    page_number: int | None = Field(default=None, ge=1)
    chunk_type: ChunkType
    #: The source text exactly as it appears in the document — never the
    #: normalised/dediacritised form used for retrieval, or the highlight will
    #: not align with the rendered PDF.
    quoted_text: str
    #: Character offsets within the chunk, for precise highlighting.
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    #: Heading breadcrumb, e.g. "Section 3 > 3.2 Technical > 3.2.14".
    section_path: str | None = None
    #: Reranker score for the chunk this citation came from.
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_span(self) -> Citation:
        if self.start_char is not None and self.end_char is not None:
            if self.end_char <= self.start_char:
                raise ValueError("end_char must be greater than start_char")
        return self


class GenerationRequest(StrictModel):
    """Ask for a draft answer to one requirement."""

    requirement_ref: str = Field(min_length=1, max_length=128)
    requirement_text: str = Field(min_length=1)
    section_path: str | None = Field(default=None, max_length=512)
    is_mandatory: bool = True
    language: Language | None = Field(
        default=None, description="Defaults to the workspace response language"
    )
    #: Optional reviewer steer ("emphasise our ISO 27001 certification").
    #: Additional instruction only — it can never widen the evidence set.
    style_hint: str | None = Field(default=None, max_length=500)


class BatchGenerationRequest(StrictModel):
    """Draft answers for many requirements in one enqueue."""

    requirements: list[GenerationRequest] = Field(min_length=1, max_length=500)
    #: Re-draft requirements that already have a current answer.
    overwrite_existing: bool = False


class ProposalEdit(StrictModel):
    """Reviewer's edit. Never overwrites ``answer_text``.

    The generated original is preserved so the human-vs-model delta stays
    measurable — that ratio is the honest quality metric for the product.
    """

    edited_text: str = Field(min_length=1)
    review_notes: str | None = Field(default=None, max_length=2000)
    #: Optimistic-lock token; must match the row's current ``version``.
    #: A mismatch returns 409 rather than silently discarding a co-reviewer's
    #: concurrent save.
    expected_version: int = Field(ge=1)


class ProposalReviewAction(StrictModel):
    """Approve, reject, or escalate. The human-in-the-loop gate."""

    action: ProposalStatus = Field(
        description="One of: approved, rejected, needs_sme, pending_review"
    )
    review_notes: str | None = Field(default=None, max_length=2000)
    assigned_sme_id: uuid.UUID | None = None
    expected_version: int = Field(ge=1)
    #: Approve an answer that carries no citations. Off by default, and the
    #: service refuses without it: the product's one hard promise is that
    #: nothing ships without evidence from the customer's own documents. A
    #: reviewer who wrote the answer themselves is a legitimate case, but it
    #: must be distinguishable in the audit trail from a grounded answer, so it
    #: costs a deliberate flag and a written note.
    acknowledge_ungrounded: bool = False

    @model_validator(mode="after")
    def _validate_action(self) -> ProposalReviewAction:
        allowed = {
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
            ProposalStatus.NEEDS_SME,
            ProposalStatus.PENDING_REVIEW,
        }
        if self.action not in allowed:
            raise ValueError(f"action must be one of {sorted(a.value for a in allowed)}")
        if self.action is ProposalStatus.NEEDS_SME and self.assigned_sme_id is None:
            raise ValueError("assigned_sme_id is required when escalating to an SME")
        if self.action is ProposalStatus.REJECTED and not self.review_notes:
            raise ValueError("review_notes are required when rejecting")
        if self.acknowledge_ungrounded and self.action is not ProposalStatus.APPROVED:
            raise ValueError("acknowledge_ungrounded only applies to approval")
        return self


class GroundingMetrics(APIModel):
    """Evidence quality, surfaced to the reviewer rather than hidden in logs.

    A reviewer who can see *why* the system is unsure triages far faster than
    one handed a bare percentage.
    """

    verdict: GroundingVerdict
    citation_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    top_retrieval_score: float | None = None
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_count: int = 0
    #: Sentences the verifier could not tie to any citation. These are exactly
    #: what the reviewer must check, and the UI highlights them inline.
    unsupported_sentences: list[str] = Field(default_factory=list)


class ProposalRead(TimestampedRead):
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    requirement_ref: str
    requirement_text: str
    section_path: str | None
    is_mandatory: bool

    answer_text: str | None
    edited_text: str | None
    language: Language

    citations: list[Citation]
    grounding_verdict: GroundingVerdict
    citation_coverage: float | None
    top_retrieval_score: float | None
    confidence_score: float | None
    abstention_reason: str | None

    status: ProposalStatus
    reviewed_by_id: uuid.UUID | None
    reviewed_at: datetime | None
    review_notes: str | None
    assigned_sme_id: uuid.UUID | None

    version: int
    is_current: bool
    model_id: str | None

    @computed_field
    @property
    def final_text(self) -> str | None:
        """What export will actually emit."""
        return self.edited_text or self.answer_text

    @computed_field
    @property
    def was_edited_by_human(self) -> bool:
        return self.edited_text is not None and self.edited_text != self.answer_text

    @computed_field
    @property
    def requires_human_edit(self) -> bool:
        return self.grounding_verdict in (
            GroundingVerdict.PARTIAL,
            GroundingVerdict.UNVERIFIED,
        )


class ProposalSummary(APIModel):
    """Row projection for the review queue — answer bodies stay out of a
    200-row list response.
    """

    id: uuid.UUID
    requirement_ref: str
    is_mandatory: bool
    status: ProposalStatus
    grounding_verdict: GroundingVerdict
    confidence_score: float | None
    citation_count: int
    version: int


class ComplianceMatrixRow(APIModel):
    """One row of the exported traceability matrix.

    This is the artefact a procurement authority audits: every requirement,
    its answer, who approved it, and which source documents back it.
    """

    requirement_ref: str
    requirement_text: str
    is_mandatory: bool
    answer_text: str | None
    status: ProposalStatus
    confidence_score: float | None
    source_documents: list[str]
    reviewed_by: str | None
    reviewed_at: datetime | None
