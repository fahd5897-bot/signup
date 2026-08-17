"""Tenant (customer company) — the root of every ownership chain."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, CheckConstraint, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin, pg_enum
from app.db.models.enums import Language, TenantPlan

if TYPE_CHECKING:
    from app.db.models.document import Document
    from app.db.models.user import User
    from app.db.models.workspace import Workspace


class Tenant(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A customer organisation. Deliberately *not* a :class:`TenantMixin` model —
    this is the table the discriminator points at, so it carries no ``tenant_id``
    of its own and is the one table exempt from Row-Level Security.
    """

    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("seats_limit > 0", name="seats_limit_positive"),
        CheckConstraint("storage_quota_bytes >= 0", name="storage_quota_non_negative"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: URL-safe identifier used for tenant-scoped routes and S3 key prefixes.
    slug: Mapped[str] = mapped_column(String(63), nullable=False, unique=True, index=True)

    plan: Mapped[TenantPlan] = mapped_column(
        pg_enum(TenantPlan, "tenant_plan"),
        default=TenantPlan.TRIAL,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ------------------------------------------------------------ localisation
    #: Default UI locale for new members; individual users may override.
    default_locale: Mapped[Language] = mapped_column(
        pg_enum(Language, "language"),
        default=Language.EN,
        nullable=False,
    )

    # ------------------------------------------------------------------ quotas
    # Enforced by QuotaService before work is enqueued, never after — a rejected
    # upload must not have already cost an embedding call.
    seats_limit: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    storage_quota_bytes: Mapped[int] = mapped_column(
        Integer().with_variant(__import__("sqlalchemy").BigInteger(), "postgresql"),
        default=10 * 1024**3,
        nullable=False,
    )
    monthly_generation_limit: Mapped[int] = mapped_column(Integer, default=1_000, nullable=False)

    # ------------------------------------------------------------- integrations
    #: SSO/OIDC issuer for enterprise tenants; NULL means password auth.
    sso_issuer_url: Mapped[str | None] = mapped_column(String(512), default=None)
    #: Branding for exported DOCX/PDF (logo URL, colours, letterhead template id).
    branding: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    # ----------------------------------------------------------- relationships
    users: Mapped[list[User]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", passive_deletes=True
    )
    workspaces: Mapped[list[Workspace]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", passive_deletes=True
    )
    documents: Mapped[list[Document]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", passive_deletes=True
    )
