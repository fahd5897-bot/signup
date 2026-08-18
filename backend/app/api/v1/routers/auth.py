"""Authentication endpoints.

Tokens are returned in the JSON body **and** set as httpOnly cookies. The
cookie is what the browser client uses — it is unreadable from JavaScript, so
an XSS cannot exfiltrate the session. The body is for non-browser clients
(the CLI, integration tests, a future mobile app) which have no cookie jar.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Cookie, Depends, Response, status

from app.api.v1.deps.auth import AuthenticationError, CurrentUser, get_current_user
from app.core.config import Settings, get_settings
from app.schemas.auth import (
    AuthenticatedUser,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
)
from app.security.tokens import REFRESH_TTL
from app.services.auth_service import AuthResult, AuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


def _set_auth_cookies(response: Response, result: AuthResult, settings: Settings) -> None:
    """Write the session cookies.

    ``samesite="lax"`` rather than ``"none"``: the frontend is served
    same-origin behind a rewrite in the recommended deployment, so a
    third-party cookie is unnecessary — and ``"none"`` would require
    ``secure`` plus expose the session to any cross-site navigation.
    """
    secure = settings.environment != "local"

    response.set_cookie(
        ACCESS_COOKIE,
        result.access_token,
        max_age=result.expires_in,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        result.refresh_token,
        max_age=int(REFRESH_TTL.total_seconds()),
        httponly=True,
        secure=secure,
        samesite="lax",
        # Scoped to the refresh endpoint only, so the long-lived credential is
        # not attached to every API request it has no business authorising.
        path="/api/v1/auth/refresh",
    )


def _to_pair(result: AuthResult) -> TokenPair:
    return TokenPair(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.expires_in,
        user=AuthenticatedUser(
            id=result.user_id,
            tenant_id=result.tenant_id,
            email=result.email,
            role=result.role,
        ),
    )


@router.post(
    "/register",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organisation and its first user",
)
async def register(
    payload: RegisterRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    """Sign up. The creator becomes the organisation's owner."""
    result = await AuthService(settings).register_organisation(
        organisation_name=payload.organisation_name,
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password.get_secret_value(),
    )
    _set_auth_cookies(response, result, settings)
    return _to_pair(result)


@router.post("/login", response_model=TokenPair, summary="Sign in")
async def login(
    payload: LoginRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    result = await AuthService(settings).authenticate(
        email=payload.email,
        password=payload.password.get_secret_value(),
        tenant_slug=payload.tenant_slug,
    )
    _set_auth_cookies(response, result, settings)
    return _to_pair(result)


@router.post("/refresh", response_model=TokenPair, summary="Exchange a refresh token")
async def refresh(
    response: Response,
    payload: RefreshRequest | None = None,
    refresh_token: str | None = Cookie(default=None),
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    """Exchange a refresh token for a new pair.

    **The cookie is the browser's only way in.** It is httpOnly, so JavaScript
    cannot read it to put it in a body — a refresh endpoint that accepted only
    a body would be unreachable from the very client the cookie exists for, and
    every session would simply die at the fifteen-minute access-token
    expiry with a valid thirty-day credential sitting unused.

    The body is still accepted, for clients with no cookie jar: the CLI, tests,
    a future mobile app. An explicitly presented token wins over the ambient
    one, matching how ``get_current_user`` resolves the access token.
    """
    token = payload.refresh_token if payload else refresh_token
    if not token:
        raise AuthenticationError("no refresh token supplied")

    result = await AuthService(settings).refresh(token)
    _set_auth_cookies(response, result, settings)
    return _to_pair(result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Sign out")
async def logout(response: Response) -> None:
    """Clear the session cookies.

    Access tokens stay valid until they expire — they are stateless by design.
    The 15-minute lifetime is what bounds the exposure; revoking individual
    tokens needs a denylist keyed on ``jti``, which is deliberately deferred
    until there is a reason to pay for the lookup on every request.
    """
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth/refresh")


@router.get("/me", response_model=AuthenticatedUser, summary="Current user")
async def me(user: CurrentUser = Depends(get_current_user)) -> AuthenticatedUser:
    return AuthenticatedUser(id=user.id, tenant_id=user.tenant_id, email=user.email, role=user.role)
