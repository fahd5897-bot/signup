"""Liveness and readiness must not be the same check.

Conflating them is how a brief database blip becomes a restart loop: the
orchestrator kills a perfectly healthy process because a dependency it does not
control was slow.
"""

from __future__ import annotations

import httpx
import pytest
from app.api.middleware.error_handler import register_exception_handlers
from app.api.v1.routers import health as health_router
from fastapi import FastAPI


@pytest.fixture
def client(settings):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(health_router.router)
    return app


async def _get(app, path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


async def test_liveness_touches_no_dependency(client, monkeypatch):
    """Proven by making every dependency explode."""

    def _explode(*args, **kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(health_router, "get_engine", _explode)

    response = await _get(client, "/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_is_503_when_a_dependency_is_down(client, monkeypatch):
    """Load balancers read the status code, not a flag in the body."""

    def _explode(*args, **kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(health_router, "get_engine", _explode)

    response = await _get(client, "/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    # Which dependency failed is the first thing anyone needs at 3am.
    assert body["checks"]["postgres"] == "unavailable"


async def test_readiness_reports_every_dependency_not_just_the_first(client, monkeypatch):
    def _explode(*args, **kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(health_router, "get_engine", _explode)

    response = await _get(client, "/ready")
    checks = response.json()["checks"]
    assert set(checks) == {"postgres", "qdrant"}
