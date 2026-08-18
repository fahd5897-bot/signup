"""Authentication request/response contracts."""

from __future__ import annotations

import uuid

from pydantic import EmailStr, Field, SecretStr, field_validator

from app.db.models.enums import UserRole
from app.schemas.base import APIModel, StrictModel

#: Length beats composition rules for real-world strength, so there is
#: deliberately no symbol or digit requirement — those push people toward
#: predictable substitutions rather than longer passwords.
MIN_PASSWORD_LENGTH = 12


def _check_password(value: SecretStr) -> SecretStr:
    secret = value.get_secret_value()
    if len(secret) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return value


class RegisterRequest(StrictModel):
    organisation_name: str = Field(min_length=2, max_length=255)
    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: SecretStr

    _validate_password = field_validator("password")(_check_password)

    @field_validator("email")
    @classmethod
    def _normalise(cls, v: str) -> str:
        return v.lower()


class LoginRequest(StrictModel):
    email: EmailStr
    password: SecretStr
    #: Required only when the same email exists in more than one organisation.
    tenant_slug: str | None = Field(default=None, max_length=63)

    @field_validator("email")
    @classmethod
    def _normalise(cls, v: str) -> str:
        return v.lower()


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=1)


class AuthenticatedUser(APIModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: EmailStr
    role: UserRole


class TokenPair(APIModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - scheme name, not a secret
    expires_in: int = Field(description="Access-token lifetime in seconds")
    user: AuthenticatedUser
