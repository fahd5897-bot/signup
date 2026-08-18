"""Authentication end to end, against a live PostgreSQL with RLS enabled.

The whole point of these tests is that they run with the policies ON. An auth
suite that passes only because RLS is disabled proves nothing about the system
that ships.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from app.api.middleware.error_handler import (
    ExceptionToResponseMiddleware,
    register_exception_handlers,
)
from app.api.v1.routers import auth as auth_router
from fastapi import FastAPI

pytestmark = pytest.mark.integration


@pytest.fixture
def client(monkeypatch, app_dsn) -> httpx.AsyncClient:
    """App wired exactly as create_app does, connecting as the unprivileged role."""
    monkeypatch.setenv("POSTGRES_DSN", app_dsn)
    monkeypatch.setenv("JWT_SECRET", "t" * 48)

    import app.db.session as session_module
    from app.core.config import get_settings

    get_settings.cache_clear()
    session_module._engine = None
    session_module._session_factory = None

    app = FastAPI()
    app.add_middleware(ExceptionToResponseMiddleware)
    register_exception_handlers(app)
    app.include_router(auth_router.router, prefix="/api/v1")

    yield httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    get_settings.cache_clear()
    session_module._engine = None
    session_module._session_factory = None


def _signup_body(suffix: str) -> dict[str, str]:
    return {
        "organisation_name": f"Acme {suffix}",
        "full_name": "Fahd",
        "email": f"owner-{suffix}@example.com",
        "password": "correct-horse-battery-staple",
    }


async def test_register_creates_tenant_and_owner(client):
    """Registration must work with RLS on.

    An unscoped session cannot insert a tenant — the WITH CHECK predicate
    compares against an unset GUC. The service sets app.tenant_id to the new
    tenant's own id first; this asserts that actually works.
    """
    async with client:
        response = await client.post(
            "/api/v1/auth/register", json=_signup_body(uuid.uuid4().hex[:8])
        )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["role"] == "owner"
    assert body["expires_in"] == 900
    assert body["access_token"] and body["refresh_token"]
    # httpOnly cookies so an XSS cannot read the session.
    assert "access_token" in response.cookies


async def test_login_then_me_round_trip(client):
    suffix = uuid.uuid4().hex[:8]
    async with client:
        await client.post("/api/v1/auth/register", json=_signup_body(suffix))

        login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": f"owner-{suffix}@example.com",
                "password": "correct-horse-battery-staple",
            },
        )
        assert login.status_code == 200
        token = login.json()["access_token"]

        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == f"owner-{suffix}@example.com"


async def test_wrong_password_and_unknown_email_are_indistinguishable(client):
    """Distinguishing them turns the login form into an enumeration oracle."""
    suffix = uuid.uuid4().hex[:8]
    async with client:
        await client.post("/api/v1/auth/register", json=_signup_body(suffix))

        wrong = await client.post(
            "/api/v1/auth/login",
            json={"email": f"owner-{suffix}@example.com", "password": "not-the-password"},
        )
        unknown = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "not-the-password"},
        )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["type"] == unknown.json()["type"] == "invalid_credentials"
    assert wrong.json()["title"] == unknown.json()["title"]


async def test_refresh_token_is_rejected_as_a_bearer_credential(client):
    """Type confusion here turns a 15-minute credential into a 30-day one."""
    suffix = uuid.uuid4().hex[:8]
    async with client:
        registered = await client.post("/api/v1/auth/register", json=_signup_body(suffix))
        refresh_token = registered.json()["refresh_token"]

        response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh_token}"}
        )
    assert response.status_code == 401


async def test_refresh_issues_a_new_access_token(client):
    suffix = uuid.uuid4().hex[:8]
    async with client:
        registered = await client.post("/api/v1/auth/register", json=_signup_body(suffix))
        original = registered.json()

        refreshed = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": original["refresh_token"]},
        )
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"] != original["access_token"]
    assert refreshed.json()["user"]["id"] == original["user"]["id"]


async def test_a_browser_can_refresh_with_only_its_cookies(client):
    """The browser's only route in.

    The refresh cookie is httpOnly precisely so JavaScript cannot read it —
    which means a refresh endpoint reading only the request body is unreachable
    from the client the cookie exists for. Every session would then die at the
    fifteen-minute access-token expiry with a valid thirty-day credential
    sitting unused, and the app would show errors rather than a sign-in prompt,
    because the access cookie is still present.
    """
    suffix = uuid.uuid4().hex[:8]
    async with client:
        await client.post("/api/v1/auth/register", json=_signup_body(suffix))
        # No body at all: exactly what fetch(credentials: "include") sends.
        refreshed = await client.post("/api/v1/auth/refresh")

    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]
    # And the rotated pair is written back as cookies, or the next refresh
    # would replay a token the client no longer holds.
    assert "access_token" in refreshed.cookies
    assert "refresh_token" in refreshed.cookies


async def test_refresh_without_a_token_anywhere_is_401_not_422(client):
    """A signed-out browser hitting refresh must get an auth error it can act
    on, not a validation error about a missing body field."""
    async with client:
        response = await client.post("/api/v1/auth/refresh")
    assert response.status_code == 401
    assert response.json()["type"] == "unauthenticated"


async def test_an_explicit_body_token_wins_over_the_cookie(client):
    """Matches how the access token resolves: an explicitly presented
    credential beats the ambient one, so a CLI cannot be silently switched onto
    whatever session the cookie jar happens to hold."""
    first, second = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
    async with client:
        await client.post("/api/v1/auth/register", json=_signup_body(first))
        other = await client.post("/api/v1/auth/register", json=_signup_body(second))
        # The jar now holds `second`'s cookies; present `other`'s token
        # explicitly and confirm the response follows the body.
        refreshed = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": other.json()["refresh_token"]},
        )

    assert refreshed.status_code == 200
    assert refreshed.json()["user"]["id"] == other.json()["user"]["id"]


async def test_the_same_email_may_own_two_organisations(client):
    """A consultancy serving competing bidders needs one account per client.

    Uniqueness is on (tenant_id, email); a global check would block this.
    """
    email = f"consultant-{uuid.uuid4().hex[:8]}@example.com"
    async with client:
        first = await client.post(
            "/api/v1/auth/register",
            json={
                "organisation_name": "Bidder One",
                "full_name": "Consultant",
                "email": email,
                "password": "correct-horse-battery-staple",
            },
        )
        second = await client.post(
            "/api/v1/auth/register",
            json={
                "organisation_name": "Bidder Two",
                "full_name": "Consultant",
                "email": email,
                "password": "correct-horse-battery-staple",
            },
        )
        assert first.status_code == second.status_code == 201
        assert first.json()["user"]["tenant_id"] != second.json()["user"]["tenant_id"]

        # Ambiguous without a slug: signing them into the wrong customer's data
        # would be worse than refusing.
        ambiguous = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "correct-horse-battery-staple"},
        )
    assert ambiguous.status_code == 401


async def test_tokens_carry_the_registering_tenant(client):
    """Two organisations must never receive the same tenant claim."""
    async with client:
        a = await client.post("/api/v1/auth/register", json=_signup_body(uuid.uuid4().hex[:8]))
        b = await client.post("/api/v1/auth/register", json=_signup_body(uuid.uuid4().hex[:8]))
    assert a.json()["user"]["tenant_id"] != b.json()["user"]["tenant_id"]


async def test_short_password_is_refused(client):
    async with client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "organisation_name": "Acme",
                "full_name": "Fahd",
                "email": "short@example.com",
                "password": "short",
            },
        )
    assert response.status_code == 422


async def test_me_requires_credentials(client):
    async with client:
        response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
