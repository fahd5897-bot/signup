"""GeneratedProposal — one grounded answer plus its evidence and review state.

This is the table the zero-hallucination guarantee is actually recorded in.
Every column below either carries evidence, records a gate decision, or names
the human who accepted responsibility for the text.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from app.db.models.enums import GroundingVerdict, Language, ProposalStatus

if TYPE_CHECKING:
    from app.db.models.user import User
    from app.db.models.workspace import Workspace


class GeneratedProposal(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, SoftDeleteMixin):
    """A drafted response to one requirement of a tender.

    **Why citations are JSONB rather than a child table.** A citation is an
    immutable byte-range fact produced by the Anthropic API alongside the text
    it justifies. It is only ever read as a complete set with its answer, and
    never queried across proposals. Normalising it would add a join to the
    hottest read path in the product (the review workbench) and buy nothing.
    The shape is pinned by the ``Citation`` Pydantic model and validated on
    write; a GIN index supports the one cross-cutting query that does matter —
    "which answers cite this document?" — for impact analysis when a source is
    superseded.

    **Version history** is likewise not a child table: ``previous_version_id``
    forms a linked list, so an edit is a new immutable row rather than an
    update. A submitted tender response must be reconstructible exactly as it
    was approved, which an in-place edit destroys.
    """

    __tablename__ = "generated_proposals"
    __extra_table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_proposals_workspace_same_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reviewed_by_id"],
            ["users.tenant_id", "users.id"],
            name="fk_proposals_reviewer_same_tenant",
            ondelete="SET NULL",
        ),
        # Exactly one live version per requirement per workspace. Partial, so
        # superseded versions and soft-deleted rows are exempt.
        Index(
            "uq_proposals_current_requirement",
            "workspace_id",
            "requirement_ref",
            unique=True,
            postgresql_where=Text("is_current AND deleted_at IS NULL"),
        ),
        # The review queue's default ordering.
        Index("ix_proposals_workspace_status", "workspace_id", "status"),
        # "Which answers cite this document?" — impact analysis on source updates.
        Index("ix_proposals_citations_gin", "citations", postgresql_using="gin"),
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)",
            name="confidence_in_range",
        ),
        CheckConstraint(
            "citation_coverage IS NULL OR (citation_coverage >= 0 AND citation_coverage <= 1)",
            name="coverage_in_range",
        ),
        # The core invariant: approval requires a named human and a timestamp.
        # No code path can produce an approved answer without accountability.
        CheckConstraint(
            "status <> 'approved' OR (reviewed_by_id IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="approved_requires_reviewer",
        ),
        # An abstention must say why, and must carry no answer text.
        CheckConstraint(
            "status <> 'abstained' OR (abstention_reason IS NOT NULL AND answer_text IS NULL)",
            name="abstained_requires_reason",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, index=True
    )

    # -------------------------------------------------------- the requirement
    #: Reference as printed in the tender ("3.2.14", "Annex B Q7"). Stable
    #: across regenerations, which is what makes the compliance matrix stable.
    requirement_ref: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    section_path: Mapped[str | None] = mapped_column(String(512), default=None)
    is_mandatory: Mapped[bool] = mapped_column(default=True, nullable=False)

    # ------------------------------------------------------------- the answer
    #: NULL when the retrieval gate abstained — there is deliberately no text to
    #: show, because an unsupported placeholder is what reviewers rubber-stamp.
    answer_text: Mapped[str | None] = mapped_column(Text, default=None)
    #: Reviewer's edited text. The generated original is never overwritten, so
    #: the human-vs-model delta stays measurable.
    edited_text: Mapped[str | None] = mapped_column(Text, default=None)
    language: Mapped[Language] = mapped_column(
        pg_enum(Language, "language", create_type=False),
        default=Language.EN,
        nullable=False,
    )

    # ----------------------------------------------------------- the evidence
    #: List of ``Citation`` objects: document_id, page_number, chunk_type,
    #: cited_text, and the char offsets returned by the Anthropic citations API.
    #: Offsets are computed server-side against the supplied document bytes, so
    #: a fabricated citation fails to resolve instead of passing silently.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    #: Qdrant point IDs retrieved for this answer, kept for reproducibility:
    #: a disputed answer can be re-derived from the exact evidence set used.
    retrieved_chunk_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    # ------------------------------------------------------- grounding metrics
    grounding_verdict: Mapped[GroundingVerdict] = mapped_column(
        pg_enum(GroundingVerdict, "grounding_verdict"),
        default=GroundingVerdict.NOT_APPLICABLE,
        nullable=False,
        index=True,
    )
    #: Fraction of factual sentences carrying a resolving citation.
    citation_coverage: Mapped[float | None] = mapped_column(default=None)
    #: Best reranker score across the retrieved set.
    top_retrieval_score: Mapped[float | None] = mapped_column(default=None)
    #: Composite of retrieval, rerank, coverage, and verifier verdict. Advisory
    #: for reviewer triage — it never gates anything on its own.
    confidence_score: Mapped[float | None] = mapped_column(default=None)
    #: Set when the retrieval gate refused to call the model at all.
    abstention_reason: Mapped[str | None] = mapped_column(Text, default=None)

    # ------------------------------------------------- human-in-the-loop state
    status: Mapped[ProposalStatus] = mapped_column(
        pg_enum(ProposalStatus, "proposal_status"),
        default=ProposalStatus.DRAFT,
        nullable=False,
        index=True,
    )
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    review_notes: Mapped[str | None] = mapped_column(Text, default=None)
    assigned_sme_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), default=None)

    # -------------------------------------------------------------- versioning
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_current: Mapped[bool] = mapped_column(default=True, nullable=False)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), default=None
    )

    # ------------------------------------------------------------ provenance
    #: Exact model ID that produced this text (e.g. "claude-opus-5"). Required
    #: for reproducibility and for re-running evaluations after a model change.
    model_id: Mapped[str | None] = mapped_column(String(64), default=None)
    prompt_version: Mapped[str | None] = mapped_column(String(32), default=None)
    input_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    output_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    generation_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    #: Langfuse trace ID — links this row to the full prompt/response record.
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None)

    # ----------------------------------------------------------- relationships
    workspace: Mapped[Workspace] = relationship(back_populates="proposals")
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by_id], viewonly=True)

    # SQLAlchemy optimistic locking. Two reviewers opening the same answer in
    # the workbench is routine; without this the second save silently discards
    # the first. With it, the loser gets a StaleDataError and a merge prompt.
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}

    @property
    def final_text(self) -> str | None:
        """Text that will be exported: the reviewer's edit if present."""
        return self.edited_text or self.answer_text

    @property
    def was_edited_by_human(self) -> bool:
        return self.edited_text is not None and self.edited_text != self.answer_text

    @property
    def requires_human_edit(self) -> bool:
        """Coverage gate outcome — the second fail-closed door (ARCHITECTURE §4)."""
        return self.grounding_verdict in (GroundingVerdict.PARTIAL, GroundingVerdict.UNVERIFIED)
