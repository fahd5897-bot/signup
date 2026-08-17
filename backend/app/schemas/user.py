"""User schemas.

The password never appears in any outbound model, and ``tenant_id`` never
appears in any inbound one — it is taken from the verified token.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, SecretStr, field_validator

from app.db.models.enums import Language, UserRole
from app.schemas.base import APIModel, StrictModel, TimestampedRead

#: Minimum password length. Length dominates composition rules for real-world
#: strength, so there is deliberately no symbol/digit requirement — those push
#: users toward predictable substitutions.
MIN_PASSWORD_LENGTH = 12


class UserCreate(StrictModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    password: SecretStr | None = Field(default=None, description="Omit for SSO-provisioned users")
    role: UserRole = UserRole.VIEWER
    locale: Language = Language.EN

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and len(v.get_secret_value()) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
        return v

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, v: str) -> str:
        """Lower-case so the (tenant_id, email) unique constraint behaves
        case-insensitively without needing a functional index or CITEXT.
        """
        return v.lower()


class UserUpdate(StrictModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    locale: Language | None = None
    is_active: bool | None = None


class UserRoleUpdate(StrictModel):
    """Role changes are a separate endpoint from profile edits.

    Privilege escalation deserves its own audited route and its own permission
    check; folding it into a general PATCH is how "update your display name"
    quietly becomes "make yourself an owner".
    """

    role: UserRole


class UserRead(TimestampedRead):
    tenant_id: uuid.UUID
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    locale: Language
    last_login_at: datetime | None = None
    # hashed_password and external_subject_id are never serialised.


class LoginRequest(StrictModel):
    email: EmailStr
    password: SecretStr
    #: Required when the same email exists in several tenants.
    tenant_slug: str | None = None


class TokenPair(APIModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - literal scheme name, not a secret
    expires_in: int = Field(description="Access-token lifetime in seconds")
