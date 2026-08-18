"""Extraction to compliance matrix, end to end.

Against a real PostgreSQL with RLS enforced and a real (in-memory) Qdrant, so
the scroll filter, the payload contract, and the row writes are all exercised
as they run in production. The one thing stubbed is the model call itself.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from app.db.models.enums import DocumentRole, DocumentStatus, ProposalStatus
from app.db.repositories.documents import DocumentRepository
from app.db.repositories.proposals import ProposalRepository
from app.db.session import tenant_session
from app.rag.prompts.requirement_extraction import TOOL_NAME
from app.rag.vectorstore.schema import ChunkPayload, VectorName
from app.services.requirement_service import (
    DocumentNotReadyError,
    NotATenderError,
    RequirementService,
)
from app.services.review_service import ReviewService
from qdrant_client import models
from sqlalchemy import text

pytestmark = pytest.mark.integration

CLAUSE_A = (
    "3.2.14 The contractor shall hold a valid ISO 27001 certificate for the "
    "duration of the contract."
)
CLAUSE_B = "3.2.15 The bidder may attach reference projects from the last five years."


@pytest.fixture(autouse=True)
def _app_role(monkeypatch, app_dsn):
    monkeypatch.setenv("POSTGRES_DSN", app_dsn)
    import app.db.session as session_module
    from app.core.config import get_settings

    def _reset() -> None:
        get_settings.cache_clear()
        session_module._engine = None
        session_module._session_factory = None

    _reset()
    yield
    _reset()


@pytest.fixture
async def workspace(superuser_engine, two_tenants):
    a, _ = two_tenants
    workspace_id = uuid.uuid4()
    async with superuser_engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(a)})
        owner_id = (
            await conn.execute(text("SELECT id FROM users WHERE tenant_id = :t LIMIT 1"), {"t": a})
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO workspaces (id,tenant_id,name,status,response_language,"
                "owner_id,grounding_config,requirements_total,requirements_approved) "
                "VALUES (:w,:t,'Tender','draft','en',:o,'{}',0,0)"
            ),
            {"w": workspace_id, "t": a, "o": owner_id},
        )
    return a, workspace_id, owner_id


async def _document(
    tenant_id: uuid.UUID,
    *,
    role: DocumentRole = DocumentRole.TENDER,
    status: DocumentStatus = DocumentStatus.READY,
) -> uuid.UUID:
    async with tenant_session(tenant_id) as session:
        repository = DocumentRepository(session)
        document = await repository.create(
            tenant_id=tenant_id,
            filename="tender.pdf",
            mime_type="application/pdf",
            size_bytes=2048,
            content_sha256=uuid.uuid4().hex * 2,
            storage_key="k",
            role=role,
        )
        document_id = document.id
        if status is not DocumentStatus.UPLOADED:
            document.status = status
        return document_id


async def _index(qdrant, settings, tenant_id: uuid.UUID, document_id: uuid.UUID, *texts: str):
    """Write chunks exactly as the ingestion pipeline does."""
    points = []
    for index, body in enumerate(texts):
        payload = ChunkPayload(
            tenant_id=str(tenant_id),
            workspace_id=None,
            document_id=str(document_id),
            document_name="tender.pdf",
            document_role=DocumentRole.TENDER,
            page_number=index + 1,
            chunk_type="narrative",
            chunk_index=index,
            text=body.casefold(),
            raw_text=body,
            language="en",
            section_path="Section 3",
        )
        points.append(
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector={
                    VectorName.DENSE: [0.01] * settings.embedding_dim,
                    VectorName.SPARSE: models.SparseVector(indices=[1], values=[1.0]),
                },
                payload=payload.to_qdrant(),
            )
        )
    await qdrant.upsert(collection_name=settings.qdrant_collection, points=points)


def _extraction_response(*requirements: dict):
    return SimpleNamespace(
        model="claude-haiku-4-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        content=[
            SimpleNamespace(
                type="tool_use", name=TOOL_NAME, input={"requirements": list(requirements)}
            )
        ],
    )


def _service(monkeypatch, qdrant, settings, response) -> RequirementService:
    service = RequirementService(qdrant, anthropic=object(), settings=settings)

    async def _fake_call(extract, language, index, total):
        return response

    monkeypatch.setattr(service._chain, "_call", _fake_call)
    return service


async def test_extraction_produces_an_unexportable_matrix(monkeypatch, qdrant, settings, workspace):
    """The whole point: a freshly read tender is 0% ready, not exportable."""
    tenant_id, workspace_id, _ = workspace
    document_id = await _document(tenant_id)
    await _index(qdrant, settings, tenant_id, document_id, CLAUSE_A, CLAUSE_B)

    service = _service(
        monkeypatch,
        qdrant,
        settings,
        _extraction_response(
            {
                "requirement_ref": "3.2.14",
                "requirement_text": "Do you hold ISO 27001?",
                "source_text": CLAUSE_A,
                "category": "technical",
                "is_mandatory": True,
            },
            {
                "requirement_ref": "3.2.15",
                "requirement_text": "Reference projects (optional).",
                "source_text": CLAUSE_B,
                "category": "administrative",
                "is_mandatory": False,
            },
        ),
    )

    registered = await service.extract_and_register(
        tenant_id=tenant_id, workspace_id=workspace_id, document_id=document_id
    )

    assert registered.created == 2
    assert registered.mandatory == 1

    async with tenant_session(tenant_id) as session:
        rows, total = await ProposalRepository(session).list_current(workspace_id=workspace_id)
    assert total == 2
    # Registered, but answered by nobody and cited by nothing.
    assert all(row.answer_text is None for row in rows)
    assert all(row.status is ProposalStatus.DRAFT for row in rows)

    progress = await ReviewService(settings).progress(
        tenant_id=tenant_id, workspace_id=workspace_id
    )
    # Only the mandatory clause counts toward the gate, and it is outstanding.
    assert (progress.total, progress.approved) == (1, 0)
    assert progress.ready_to_export is False


async def test_re_extraction_does_not_discard_existing_answers(
    monkeypatch, qdrant, settings, workspace
):
    """A reviewer's drafted work must survive re-reading the tender."""
    tenant_id, workspace_id, _ = workspace
    document_id = await _document(tenant_id)
    await _index(qdrant, settings, tenant_id, document_id, CLAUSE_A)

    response = _extraction_response(
        {
            "requirement_ref": "3.2.14",
            "requirement_text": "Do you hold ISO 27001?",
            "source_text": CLAUSE_A,
            "category": "technical",
            "is_mandatory": True,
        }
    )
    service = _service(monkeypatch, qdrant, settings, response)

    await service.extract_and_register(
        tenant_id=tenant_id, workspace_id=workspace_id, document_id=document_id
    )
    second = await service.extract_and_register(
        tenant_id=tenant_id, workspace_id=workspace_id, document_id=document_id
    )

    assert (second.created, second.skipped_existing) == (0, 1)
    async with tenant_session(tenant_id) as session:
        rows, total = await ProposalRepository(session).list_current(workspace_id=workspace_id)
    assert total == 1
    assert rows[0].version == 1, "the untouched row was not superseded"


async def test_a_knowledge_base_document_is_refused(monkeypatch, qdrant, settings, workspace):
    """Extracting from the bidder's own material fills the matrix with their
    marketing copy restated as obligations."""
    tenant_id, workspace_id, _ = workspace
    document_id = await _document(tenant_id, role=DocumentRole.KNOWLEDGE_BASE)

    service = _service(monkeypatch, qdrant, settings, _extraction_response())
    with pytest.raises(NotATenderError):
        await service.extract_and_register(
            tenant_id=tenant_id, workspace_id=workspace_id, document_id=document_id
        )


async def test_an_unfinished_document_is_refused(monkeypatch, qdrant, settings, workspace):
    """A partial matrix looks complete, which is the worst possible artefact."""
    tenant_id, workspace_id, _ = workspace
    document_id = await _document(tenant_id, status=DocumentStatus.PARSING)

    service = _service(monkeypatch, qdrant, settings, _extraction_response())
    with pytest.raises(DocumentNotReadyError):
        await service.extract_and_register(
            tenant_id=tenant_id, workspace_id=workspace_id, document_id=document_id
        )


async def test_ready_but_unindexed_is_an_error_not_an_empty_tender(
    monkeypatch, qdrant, settings, workspace
):
    """Answering '0 requirements' would read as a clean bill of health."""
    tenant_id, workspace_id, _ = workspace
    document_id = await _document(tenant_id)  # READY, but nothing indexed

    service = _service(monkeypatch, qdrant, settings, _extraction_response())
    with pytest.raises(DocumentNotReadyError):
        await service.extract_and_register(
            tenant_id=tenant_id, workspace_id=workspace_id, document_id=document_id
        )


async def test_another_tenants_chunks_are_never_read(
    monkeypatch, qdrant, settings, workspace, two_tenants
):
    """The scroll filter is the isolation boundary here, not the database."""
    tenant_id, workspace_id, _ = workspace
    _, other_tenant = two_tenants
    document_id = await _document(tenant_id)
    # Same document id, indexed under the *other* tenant.
    await _index(qdrant, settings, other_tenant, document_id, CLAUSE_A)

    service = _service(monkeypatch, qdrant, settings, _extraction_response())
    with pytest.raises(DocumentNotReadyError):
        await service.extract_and_register(
            tenant_id=tenant_id, workspace_id=workspace_id, document_id=document_id
        )
