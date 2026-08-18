"""Document — an uploaded source file and its ingestion state."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
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
from app.db.models.enums import DocumentRole, DocumentStatus, Language, ParseStrategy

if TYPE_CHECKING:
    from app.db.models.tenant import Tenant
    from app.db.models.workspace import Workspace


class Document(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, SoftDeleteMixin):
    """A file uploaded by a tenant, plus everything the ingestion pipeline
    learned about it.

    ``workspace_id`` is nullable and that nullability is meaningful:

    * **NULL** — a tenant-wide knowledge-base document (past proposals, product
      specs, certifications). Retrievable by every workspace in the tenant.
    * **set** — scoped to one pursuit (the tender notice itself, its addenda,
      confidential attachments). Retrievable only within that workspace.

    This single column is what keeps one customer's confidential tender annexes
    out of another pursuit's generated answers.
    """

    __tablename__ = "documents"
    __extra_table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name="fk_documents_workspace_same_tenant",
            ondelete="CASCADE",
        ),
        # Content-addressed de-duplication. Re-uploading an identical file
        # within a tenant reuses the existing parse rather than paying for OCR
        # and embeddings twice — a real cost on 400-page Arabic tenders.
        UniqueConstraint("tenant_id", "content_sha256", name="uq_documents_tenant_id_sha256"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint(
            "text_extraction_ratio IS NULL OR "
            "(text_extraction_ratio >= 0 AND text_extraction_ratio <= 1)",
            name="extraction_ratio_in_range",
        ),
        CheckConstraint(
            "(status <> 'failed') OR (failure_reason IS NOT NULL)",
            name="failed_requires_reason",
        ),
        Index("ix_documents_tenant_status", "tenant_id", "status"),
        Index("ix_documents_workspace_role", "workspace_id", "role"),
    )

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), default=None, index=True
    )

    # ------------------------------------------------------------ file identity
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(127), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Hex SHA-256 of the raw bytes; basis of the de-duplication constraint above.
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: Object key, always prefixed ``{tenant_id}/{document_id}/`` — the storage
    #: layer's own isolation boundary, mirroring the database's.
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)

    role: Mapped[DocumentRole] = mapped_column(
        pg_enum(DocumentRole, "document_role"),
        nullable=False,
        index=True,
    )
    status: Mapped[DocumentStatus] = mapped_column(
        pg_enum(DocumentStatus, "document_status"),
        default=DocumentStatus.PENDING,
        nullable=False,
        index=True,
    )

    # -------------------------------------------------------- ingestion results
    parse_strategy: Mapped[ParseStrategy | None] = mapped_column(
        pg_enum(ParseStrategy, "parse_strategy"),
        default=None,
    )
    #: Dominant language detected across blocks. ``MIXED`` is common and
    #: expected in GCC tenders, where Arabic clauses carry English defined terms.
    language: Mapped[Language] = mapped_column(
        pg_enum(Language, "language", create_type=False),
        default=Language.UNKNOWN,
        nullable=False,
    )
    page_count: Mapped[int | None] = mapped_column(Integer, default=None)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: Fraction of pages that produced usable text. The single most useful
    #: quality signal for Arabic scans: a low value means OCR degraded and the
    #: document should not be trusted as an evidence source. Surfaced in the UI.
    text_extraction_ratio: Mapped[float | None] = mapped_column(default=None)

    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    failure_reason: Mapped[str | None] = mapped_column(Text, default=None)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: Free-form extras from the parser (Unstructured element counts, detected
    #: table count, OCR confidence, source URL for scraped notices).
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    # ----------------------------------------------------------- relationships
    tenant: Mapped[Tenant] = relationship(back_populates="documents")
    # ``overlaps`` silences the composite-foreign-key warning rather than
    # changing behaviour: (tenant_id, workspace_id) references
    # (workspaces.tenant_id, workspaces.id), so this relationship and
    # ``tenant`` both write tenant_id. They can only ever write the same
    # value — the FK exists precisely to make a cross-tenant pairing
    # impossible — so the overlap is intended.
    workspace: Mapped[Workspace | None] = relationship(
        back_populates="documents", overlaps="tenant"
    )

    @property
    def is_global_knowledge(self) -> bool:
        """True when this document is retrievable across the whole tenant."""
        return self.workspace_id is None

    @property
    def is_retrievable(self) -> bool:
        """Only READY knowledge sources may be used to ground an answer.

        Tender documents are excluded: they are the question, not the evidence.
        """
        return self.status is DocumentStatus.READY and self.role is not DocumentRole.TENDER
