"""Authentication and tenant-context dependencies.

The single rule this module exists to enforce: **``tenant_id`` comes from the
verified token and never from the request**. No path parameter, query string, or
body field may supply it. Every inbound schema in ``app.schemas`` omits the
field entirely, and this is the only place it is produced.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Cookie, Depends, Header

from app.core.config import Settings, get_settings
from app.core.exceptions import PermissionDeniedError
from app.db.models.enums import UserRole
from app.security.tokens import InvalidTokenError, decode_token


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
    access_token: str | None = Cookie(default=None),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """Resolve the caller from an Authorization header or an httpOnly cookie.

    **The header wins.** An explicitly presented credential must take
    precedence over an ambient one: if a client sends a bearer token while a
    session cookie happens to be attached, silently honouring the cookie makes
    the request act as the wrong principal — a confused deputy, and one that is
    invisible in logs because the request looked authorised either way.

    The cookie is the browser path (httpOnly, so an XSS cannot read it); the
    header serves clients with no cookie jar — the CLI, tests, a future mobile
    app.

    Only an **access** token is accepted. `decode_token` enforces the type:
    without that check a month-long refresh token would authorise every
    request, silently turning a 15-minute credential into a 30-day one.
    """
    token: str | None = None
    scheme, _, header_token = authorization.partition(" ")
    if scheme.lower() == "bearer" and header_token:
        token = header_token
    elif access_token:
        token = access_token

    if not token:
        raise AuthenticationError("no credentials supplied")

    try:
        claims = decode_token(token, expected_type="access", settings=settings)
    except InvalidTokenError as exc:
        raise AuthenticationError(exc.detail) from exc

    try:
        return CurrentUser(
            id=claims.user_id,
            tenant_id=claims.tenant_id,
            email=claims.email,
            role=UserRole(claims.role),
        )
    except ValueError as exc:
        raise AuthenticationError("token carries an unknown role") from exc


def require_upload_permission(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not user.can_upload:
        raise PermissionDeniedError("your role cannot upload documents")
    return user
