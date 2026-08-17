"""Authentication and tenant-context dependencies.

The single rule this module exists to enforce: **``tenant_id`` comes from the
verified token and never from the request**. No path parameter, query string, or
body field may supply it. Every inbound schema in ``app.schemas`` omits the
field entirely, and this is the only place it is produced.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header

from app.core.config import Settings, get_settings
from app.core.exceptions import PermissionDeniedError
from app.db.models.enums import UserRole


@dataclass(slots=True, frozen=True)
class CurrentUser:
    """Authenticated principal, derived solely from the bearer token."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: UserRole

    @property
    def can_upload(self) -> bool:
        return self.role in (UserRole.OWNER, UserRole.BID_MANAGER, UserRole.SME)

    @property
    def can_approve(self) -> bool:
        return self.role in (UserRole.OWNER, UserRole.BID_MANAGER)


class AuthenticationError(PermissionDeniedError):
    slug = "unauthenticated"
    status_code = 401
    user_message = "Authentication required."


async def get_current_user(
    authorization: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """Decode and verify the bearer token."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError("missing bearer token")

    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("invalid token") from exc

    try:
        return CurrentUser(
            id=uuid.UUID(claims["sub"]),
            tenant_id=uuid.UUID(claims["tid"]),
            email=claims["email"],
            role=UserRole(claims["role"]),
        )
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("token is missing required claims") from exc


def require_upload_permission(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not user.can_upload:
        raise PermissionDeniedError("your role cannot upload documents")
    return user
