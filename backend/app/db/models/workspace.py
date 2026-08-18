"""Workspace — one RFP/RFQ/tender pursuit."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
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
from app.db.models.enums import Language, WorkspaceStatus

if TYPE_CHECKING:
    from app.db.models.document import Document
    from app.db.models.proposal import GeneratedProposal
    from app.db.models.tenant import Tenant
    from app.db.models.user import User


class Workspace(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, SoftDeleteMixin):
    """A single bid: the tender being answered, its documents, and its answers.

    This is the unit of collaboration and of access control below the tenant.
    Retrieval is scoped ``tenant_id AND (workspace_id = :ws OR scope = global)``
    so that a workspace sees its own tender documents plus the tenant-wide
    knowledge base, but never another pursuit's confidential attachments.
    """

    __tablename__ = "workspaces"
    __extra_table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "owner_id"],
            ["users.tenant_id", "users.id"],
            name="fk_workspaces_owner_same_tenant",
            ondelete="RESTRICT",
        ),
        # NB: deliberately no "deadline must be in the future" constraint.
        # Historical pursuits are imported for win/loss analytics (see the LOST
        # status), and a past deadline is valid data for them. Deadline sanity
        # is a warning at the API layer, not a database-level rejection.
        CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="currency_iso4217"),
        CheckConstraint(
            "requirements_approved >= 0 AND requirements_approved <= requirements_total",
            name="approved_within_total",
        ),
        Index("ix_workspaces_tenant_status", "tenant_id", "status"),
        # Partial index: the dashboard's default view is always "open pursuits".
        Index(
            "ix_workspaces_active_deadline",
            "tenant_id",
            "submission_deadline",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)

    status: Mapped[WorkspaceStatus] = mapped_column(
        pg_enum(WorkspaceStatus, "workspace_status"),
        default=WorkspaceStatus.DRAFT,
        nullable=False,
        index=True,
    )

    # ------------------------------------------------------------ tender facts
    #: Reference number printed on the tender notice, e.g. "MOH/2026/IT/0114".
    #: Not unique — authorities reissue references on re-tender.
    tender_reference: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    issuing_authority: Mapped[str | None] = mapped_column(String(255), default=None)
    submission_deadline: Mapped[date | None] = mapped_column(Date, default=None)
    estimated_value: Mapped[int | None] = mapped_column(
        Integer().with_variant(__import__("sqlalchemy").BigInteger(), "postgresql"), default=None
    )
    currency: Mapped[str | None] = mapped_column(String(3), default=None)  # ISO 4217

    #: Language the *response* must be written in. Drives prompt selection and
    #: export direction, and is independent of any reviewer's UI locale.
    response_language: Mapped[Language] = mapped_column(
        pg_enum(Language, "language", create_type=False),
        default=Language.EN,
        nullable=False,
    )

    # --------------------------------------------------------------- ownership
    #: Composite FK below ties (tenant_id, owner_id) to (users.tenant_id,
    #: users.id), so a workspace cannot be owned by a user in another tenant.
    owner_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)

    # ------------------------------------------------------------- generation
    #: Frozen copy of the grounding thresholds in force when this workspace was
    #: created. A later change to the tenant default must not retroactively
    #: alter the confidence figures a reviewer already signed off on.
    grounding_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    #: Denormalised progress counters, maintained transactionally by
    #: ReviewService. Recomputing them per dashboard render would mean an
    #: aggregate over every proposal in the tenant on every page load.
    requirements_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requirements_approved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # ----------------------------------------------------------- relationships
    tenant: Mapped[Tenant] = relationship(back_populates="workspaces")
    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    documents: Mapped[list[Document]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
        passive_deletes=True,
        # See Document.workspace: the composite FK means both this and
        # Document.tenant write tenant_id, always with the same value.
        overlaps="tenant",
    )
    proposals: Mapped[list[GeneratedProposal]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def completion_ratio(self) -> float:
        """Fraction of requirements approved. Drives the dashboard progress bar."""
        if self.requirements_total == 0:
            return 0.0
        return self.requirements_approved / self.requirements_total

    @property
    def is_exportable(self) -> bool:
        """Export gate — the fourth fail-closed door from ARCHITECTURE.md §4."""
        return self.requirements_total > 0 and self.requirements_approved == self.requirements_total
