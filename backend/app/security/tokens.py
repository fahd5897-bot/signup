"""JWT issuing and verification.

Two token types with deliberately different lifetimes:

* **access** — short-lived (15 min), carries the tenant and role claims every
  request authorises against. Never revocable, which is exactly why it is
  short.
* **refresh** — long-lived (30 days), carries almost nothing and is only good
  for minting a new access token. Its ``jti`` makes individual revocation
  possible once a denylist exists.

The ``typ`` claim is checked on every verification. Without it a refresh token
would be accepted as an access token — a month-long credential where a
15-minute one was intended.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError

TokenType = Literal["access", "refresh"]

REFRESH_TTL = timedelta(days=30)


class InvalidTokenError(AppError):
    slug = "invalid_token"
    status_code = 401
    user_message = "Your session is no longer valid. Please sign in again."


@dataclass(slots=True, frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: str
    token_type: TokenType
    jti: str


def _encode(payload: dict[str, Any], settings: Settings) -> str:
    return jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def issue_access_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    email: str,
    role: str,
    settings: Settings | None = None,
) -> tuple[str, int]:
    """Return ``(token, expires_in_seconds)``."""
    settings = settings or get_settings()
    now = datetime.now(UTC)
    ttl = settings.access_token_ttl_seconds

    token = _encode(
        {
            "sub": str(user_id),
            "tid": str(tenant_id),
            "email": email,
            "role": role,
            "typ": "access",
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + timedelta(seconds=ttl),
        },
        settings,
    )
    return token, ttl


def issue_refresh_token(
    *, user_id: uuid.UUID, tenant_id: uuid.UUID, settings: Settings | None = None
) -> str:
    """Mint a refresh token.

    Carries no email or role: those can change between issue and use, and a
    stale role baked into a month-old token is a privilege escalation waiting
    to happen. They are re-read from the database on every refresh.
    """
    settings = settings or get_settings()
    now = datetime.now(UTC)
    return _encode(
        {
            "sub": str(user_id),
            "tid": str(tenant_id),
            "typ": "refresh",
            "jti": uuid.uuid4().hex,
            "iat": now,
            "exp": now + REFRESH_TTL,
        },
        settings,
    )


def decode_token(
    token: str, *, expected_type: TokenType, settings: Settings | None = None
) -> TokenClaims:
    """Verify a token and return its claims.

    Raises:
        InvalidTokenError: expired, tampered, malformed, or the wrong type.
    """
    settings = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "tid", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError("token is invalid") from exc

    # Type confusion check. A refresh token presented as a bearer credential
    # must be refused, or the 15-minute access window silently becomes 30 days.
    if payload.get("typ") != expected_type:
        raise InvalidTokenError(f"expected a {expected_type} token, got {payload.get('typ')!r}")

    try:
        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            tenant_id=uuid.UUID(payload["tid"]),
            email=payload.get("email", ""),
            role=payload.get("role", ""),
            token_type=expected_type,
            jti=payload.get("jti", ""),
        )
    except (KeyError, ValueError) as exc:
        raise InvalidTokenError("token is missing required claims") from exc
