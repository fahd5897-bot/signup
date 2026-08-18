"""Login has to be throttled, and this is the test that says so.

Argon2id verification is deliberately expensive — roughly 64 MB and a tenth of
a second each — which is what makes a stolen hash near-worthless. It is also
what makes an unthrottled login endpoint a cheap way to exhaust the API: a few
hundred concurrent attempts saturate memory and CPU without any of them
succeeding. The same limit is what slows credential stuffing to a rate where
the timing-safe "wrong password or unknown email" answer actually holds up.

Runs against real Redis when one is reachable, because the limit has to be
shared across replicas — a per-process counter multiplies the allowance by the
number of pods, which is the same as a much weaker limit that nobody wrote down.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from app.api.middleware.error_handler import (
    ExceptionToResponseMiddleware,
    register_exception_handlers,
)
from app.api.middleware.rate_limit import AUTH_LIMIT, configure_rate_limiting
from app.api.v1.routers import auth as auth_router
from fastapi import FastAPI

pytestmark = pytest.mark.integration

#: The configured allowance, parsed from the same constant the app applies.
ALLOWED_PER_MINUTE = int(AUTH_LIMIT.split("/")[0])


@pytest.fixture
def limited_client(monkeypatch, app_dsn):
    """An app with the limiter switched on, as production runs it."""
    monkeypatch.setenv("POSTGRES_DSN", app_dsn)
    monkeypatch.setenv("JWT_SECRET", "t" * 48)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")

    import app.db.session as session_module
    from app.api.middleware import rate_limit
    from app.core.config import get_settings

    get_settings.cache_clear()
    session_module._engine = None
    session_module._session_factory = None
    settings = get_settings()

    # A key unique to this run, so a previous run's counter cannot fail this
    # test and this test cannot fail the next one.
    marker = uuid.uuid4().hex
    monkeypatch.setattr(rate_limit, "_client_key", lambda request: marker)
    rate_limit.limiter._key_func = lambda request: marker

    app = FastAPI()
    app.add_middleware(ExceptionToResponseMiddleware)
    configure_rate_limiting(app, settings)
    register_exception_handlers(app)
    app.include_router(auth_router.router, prefix="/api/v1")

    yield httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    rate_limit.limiter.enabled = False
    get_settings.cache_clear()
    session_module._engine = None
    session_module._session_factory = None


async def test_a_credential_stuffing_run_is_cut_off(limited_client):
    statuses = []
    async with limited_client:
        for i in range(ALLOWED_PER_MINUTE + 3):
            response = await limited_client.post(
                "/api/v1/auth/login",
                json={"email": f"victim-{i}@example.com", "password": "guess-guess-guess"},
            )
            statuses.append(response.status_code)

    # Every attempt is wrong, so none may succeed — but the point is that the
    # endpoint stops paying the Argon2id cost long before the run finishes.
    assert 200 not in statuses
    assert 429 in statuses
    assert statuses.count(429) >= 3


async def test_the_limit_is_reported_in_a_shape_the_client_can_branch_on(limited_client):
    """slowapi's default body is a bare string. This is one of the few errors
    the interface has to explain rather than retry, so it carries the same
    slug-and-title shape as every other error the API returns."""
    async with limited_client:
        for _ in range(ALLOWED_PER_MINUTE + 1):
            response = await limited_client.post(
                "/api/v1/auth/login",
                json={"email": "a@example.com", "password": "guess-guess-guess"},
            )

    assert response.status_code == 429
    body = response.json()
    assert body["type"] == "rate_limited"
    assert body["title"]
    assert response.headers.get("Retry-After")
