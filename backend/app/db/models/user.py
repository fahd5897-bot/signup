"""User — a member of exactly one tenant."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from app.db.models.enums import Language, UserRole

if TYPE_CHECKING:
    from app.db.models.tenant import Tenant


class User(Base, UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, SoftDeleteMixin):
    """A person within a tenant.

    Membership is single-tenant by design. Supporting one identity across
    several tenants means every query needs an active-tenant join and RLS can no
    longer key off a single session GUC. Consultancies serving multiple bidders
    get one account per client organisation instead — which also matches how
    tender confidentiality obligations are actually written.
    """

    __tablename__ = "users"
    # Email is unique *within* a tenant, not globally: the same person may
    # legitimately hold accounts in two different customer organisations.
    # TenantMixin appends its own isolation constraints around these.
    __extra_table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_id_email"),
    )

    #: Stored lower-cased and NFKC-normalised by the service layer; the unique
    #: constraint above is therefore case-insensitive in practice.
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    #: Argon2id digest. NULL for SSO-provisioned users, who have no local
    #: password — the CHECK in the migration enforces that one of the two
    #: authentication paths is always available.
    hashed_password: Mapped[str | None] = mapped_column(String(255), default=None)
    external_subject_id: Mapped[str | None] = mapped_column(String(255), default=None, index=True)

    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, "user_role"),
        default=UserRole.VIEWER,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    #: UI language. Independent of the language a proposal is written in — an
    #: Arabic-speaking reviewer may work on an English submission.
    locale: Mapped[Language] = mapped_column(
        pg_enum(Language, "language", create_type=False),
        default=Language.EN,
        nullable=False,
    )

    tenant: Mapped[Tenant] = relationship(back_populates="users")

    @property
    def can_approve(self) -> bool:
        """Only these roles may move a proposal into ``APPROVED``."""
        return self.role in (UserRole.OWNER, UserRole.BID_MANAGER)
