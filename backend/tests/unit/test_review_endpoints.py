"""HTTP contract for the review workbench.

The service tests prove the rules; these prove the rules reach the client with
the status code the UI branches on. A gate that raises correctly but surfaces
as a generic 500 is a gate the frontend cannot explain to a reviewer.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from app.api.middleware.error_handler import register_exception_handlers
from app.api.v1.routers import review as review_router
from app.core.exceptions import (
    InvalidTransitionError,
    PermissionDeniedError,
    TenantMismatchError,
    UngroundedApprovalError,
    VersionConflictError,
)
from app.security.tokens import issue_access_token
from fastapi import FastAPI


def _token(settings, role: str = "bid_manager") -> dict[str, str]:
    encoded, _ = issue_access_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="reviewer@example.com",
        role=role,
        settings=settings,
    )
    return {"Authorization": f"Bearer {encoded}"}


@pytest.fixture
def raising_client(monkeypatch, settings):
    """Build a client whose ReviewService raises whatever the test asks for."""

    def _build(exception: Exception) -> httpx.AsyncClient:
        class _FakeService:
            def __init__(self, *args, **kwargs) -> None: ...

            async def apply_action(self, **kwargs):
                raise exception

            async def apply_edit(self, **kwargs):
                raise exception

            async def get(self, **kwargs):
                raise exception

        monkeypatch.setattr(review_router, "ReviewService", _FakeService)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(review_router.router, prefix="/api/v1")
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    return _build


def _review_body(**overrides) -> dict:
    body = {"action": "approved", "expected_version": 1, "review_notes": "ok"}
    body.update(overrides)
    return body


@pytest.mark.parametrize(
    ("exception", "expected_status", "expected_slug"),
    [
        (VersionConflictError("stale"), 409, "version_conflict"),
        (InvalidTransitionError("no"), 409, "invalid_transition"),
        (UngroundedApprovalError("no evidence"), 422, "approval_blocked"),
        (PermissionDeniedError("not your call"), 403, "permission_denied"),
        (TenantMismatchError("nope"), 404, "not_found"),
    ],
)
async def test_gate_failures_reach_the_client_with_a_branchable_slug(
    raising_client, settings, exception, expected_status, expected_slug
) -> None:
    client = raising_client(exception)
    async with client:
        response = await client.post(
            f"/api/v1/proposals/{uuid.uuid4()}/review",
            headers=_token(settings),
            json=_review_body(),
        )
    assert response.status_code == expected_status
    assert response.json()["type"] == expected_slug


async def test_review_requires_authentication(raising_client, settings) -> None:
    client = raising_client(RuntimeError("should never be reached"))
    async with client:
        response = await client.post(
            f"/api/v1/proposals/{uuid.uuid4()}/review",
            json=_review_body(),
        )
    assert response.status_code == 401


async def test_expected_version_is_not_optional(raising_client, settings) -> None:
    """Without it the API would accept a decision made against unknown text."""
    client = raising_client(RuntimeError("should never be reached"))
    async with client:
        response = await client.post(
            f"/api/v1/proposals/{uuid.uuid4()}/review",
            headers=_token(settings),
            json={"action": "approved"},
        )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        # Rejection with no explanation leaves the drafter nothing to act on.
        {"action": "rejected", "expected_version": 1},
        # Escalation with nobody to escalate to.
        {"action": "needs_sme", "expected_version": 1},
        # A status that is not a review decision at all.
        {"action": "exported", "expected_version": 1},
        # The ungrounded override only makes sense on an approval.
        {
            "action": "rejected",
            "expected_version": 1,
            "review_notes": "no",
            "acknowledge_ungrounded": True,
        },
        # Unknown keys are rejected rather than silently ignored, which is what
        # stops a client smuggling {"tenant_id": ...} into the body.
        {"action": "approved", "expected_version": 1, "tenant_id": str(uuid.uuid4())},
    ],
)
async def test_malformed_decisions_are_refused_before_the_service_sees_them(
    raising_client, settings, body
) -> None:
    client = raising_client(RuntimeError("should never be reached"))
    async with client:
        response = await client.post(
            f"/api/v1/proposals/{uuid.uuid4()}/review",
            headers=_token(settings),
            json=body,
        )
    assert response.status_code == 422
