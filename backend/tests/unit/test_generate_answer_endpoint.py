"""POST /generate-answer contract."""

from __future__ import annotations

import uuid

import httpx
import jwt
import pytest
from app.api.middleware.error_handler import register_exception_handlers
from app.api.v1.routers import generation as generation_router
from app.db.models.enums import GroundingVerdict, Language, ProposalStatus
from app.rag.chains.answer_requirement import AnswerResult
from app.schemas.proposal import Citation
from fastapi import FastAPI


def _token(settings, role: str = "bid_manager") -> dict[str, str]:
    encoded = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "tid": str(uuid.uuid4()),
            "email": "bid@example.com",
            "role": role,
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {encoded}"}


def _client(monkeypatch, result: AnswerResult) -> httpx.AsyncClient:
    class _FakeChain:
        def __init__(self, *args, **kwargs) -> None: ...

        async def run(self, **kwargs):
            _FakeChain.last_kwargs = kwargs
            return result

    monkeypatch.setattr(generation_router, "AnswerChain", _FakeChain)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(generation_router.router, prefix="/api/v1")
    app.state.qdrant = object()
    app.state.anthropic = object()
    app.state.fake_chain = _FakeChain
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ), _FakeChain


@pytest.fixture
def answered() -> AnswerResult:
    return AnswerResult(
        requirement_ref="3.2.14",
        requirement_text="The Contractor shall hold ISO 27001.",
        answer_text="The company holds ISO 27001:2022, certificate IS-649201.",
        language=Language.EN,
        citations=[
            Citation(
                chunk_id="c1",
                document_id=uuid.uuid4(),
                document_name="iso-cert.pdf",
                page_number=4,
                chunk_type="narrative",
                quoted_text="certificate number IS-649201",
                relevance_score=0.93,
            )
        ],
        status=ProposalStatus.DRAFT,
        grounding_verdict=GroundingVerdict.VERIFIED,
        citation_coverage=1.0,
        top_retrieval_score=0.93,
        confidence_score=0.96,
        model_id="claude-opus-5",
    )


async def test_returns_answer_with_source_references(monkeypatch, settings, answered):
    client, _ = _client(monkeypatch, answered)
    async with client:
        response = await client.post(
            "/api/v1/generate-answer",
            headers=_token(settings),
            json={
                "requirement_ref": "3.2.14",
                "requirement_text": "The Contractor shall hold ISO 27001.",
            },
        )
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "draft"  # never auto-approved
    assert body["grounding"]["verdict"] == "verified"
    # The task's citation requirement: document name and page number.
    assert body["citations"][0]["document_name"] == "iso-cert.pdf"
    assert body["citations"][0]["page_number"] == 4
    # No phantom row fields — nothing has been persisted yet.
    assert "id" not in body and "created_at" not in body


async def test_abstention_carries_reason_and_no_answer(monkeypatch, settings):
    abstained = AnswerResult(
        requirement_ref="9.9",
        requirement_text="Nuclear decommissioning experience?",
        answer_text=None,
        language=Language.EN,
        status=ProposalStatus.ABSTAINED,
        grounding_verdict=GroundingVerdict.NOT_APPLICABLE,
        abstention_reason="no matching content in the knowledge base",
        confidence_score=0.0,
    )
    client, _ = _client(monkeypatch, abstained)
    async with client:
        response = await client.post(
            "/api/v1/generate-answer",
            headers=_token(settings),
            json={"requirement_ref": "9.9", "requirement_text": "Nuclear experience?"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "abstained"
    assert body["answer_text"] is None
    assert body["abstention_reason"]
    assert body["citations"] == []


async def test_tenant_id_comes_from_token_not_body(monkeypatch, settings, answered):
    """A caller must not be able to name someone else's tenant."""
    client, chain = _client(monkeypatch, answered)
    headers = _token(settings)
    token_tenant = uuid.UUID(
        jwt.decode(
            headers["Authorization"].split()[1],
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
        )["tid"]
    )
    attacker_tenant = str(uuid.uuid4())

    async with client:
        # A body naming another tenant is rejected outright by extra="forbid".
        rejected = await client.post(
            "/api/v1/generate-answer",
            headers=headers,
            json={
                "requirement_ref": "3.2.14",
                "requirement_text": "ISO 27001?",
                "tenant_id": attacker_tenant,
            },
        )
        assert rejected.status_code == 422

        # And the tenant the chain actually receives is the token's.
        accepted = await client.post(
            "/api/v1/generate-answer",
            headers=headers,
            json={"requirement_ref": "3.2.14", "requirement_text": "ISO 27001?"},
        )
        assert accepted.status_code == 200

    assert chain.last_kwargs["tenant_id"] == token_tenant


@pytest.mark.parametrize(
    ("role", "expected"), [("bid_manager", 200), ("sme", 200), ("viewer", 403)]
)
async def test_role_gates_generation(monkeypatch, settings, answered, role, expected):
    client, _ = _client(monkeypatch, answered)
    async with client:
        response = await client.post(
            "/api/v1/generate-answer",
            headers=_token(settings, role),
            json={"requirement_ref": "1", "requirement_text": "Q?"},
        )
    assert response.status_code == expected


async def test_requires_authentication(monkeypatch, settings, answered):
    client, _ = _client(monkeypatch, answered)
    async with client:
        response = await client.post(
            "/api/v1/generate-answer",
            json={"requirement_ref": "1", "requirement_text": "Q?"},
        )
    assert response.status_code == 401
