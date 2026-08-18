"""Removing a document has to mean removing its evidence.

The failure that matters is not a leftover row — it is a chunk that stays
searchable after the customer believes the document is gone. A superseded
certificate or another client's pricing would keep turning up as citable
evidence in answers drafted months later.
"""

from __future__ import annotations

import uuid

import pytest
from app.core.exceptions import TenantMismatchError
from app.db.models.enums import DocumentRole, DocumentStatus
from app.db.repositories.documents import DocumentRepository
from app.db.session import tenant_session
from app.rag.vectorstore.filters import build_search_filter
from app.rag.vectorstore.schema import ChunkPayload, VectorName
from app.services.document_service import DocumentService
from qdrant_client import models

pytestmark = pytest.mark.integration


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


async def _document(tenant_id: uuid.UUID) -> uuid.UUID:
    async with tenant_session(tenant_id) as session:
        document = await DocumentRepository(session).create(
            tenant_id=tenant_id,
            filename="superseded-certificate.pdf",
            mime_type="application/pdf",
            size_bytes=1024,
            content_sha256=uuid.uuid4().hex * 2,
            storage_key="k",
            role=DocumentRole.KNOWLEDGE_BASE,
        )
        document.status = DocumentStatus.READY
        return document.id


async def _index(qdrant, settings, tenant_id, document_id, count: int = 3) -> None:
    points = [
        models.PointStruct(
            id=str(uuid.uuid4()),
            vector={
                VectorName.DENSE: [0.02] * settings.embedding_dim,
                VectorName.SPARSE: models.SparseVector(indices=[1], values=[1.0]),
            },
            payload=ChunkPayload(
                tenant_id=str(tenant_id),
                workspace_id=None,
                document_id=str(document_id),
                document_name="superseded-certificate.pdf",
                document_role=DocumentRole.KNOWLEDGE_BASE,
                page_number=i + 1,
                chunk_type="narrative",
                chunk_index=i,
                text="iso 27001",
                raw_text="ISO 27001 certificate, expired.",
                language="en",
            ).to_qdrant(),
        )
        for i in range(count)
    ]
    await qdrant.upsert(collection_name=settings.qdrant_collection, points=points)


async def _chunk_count(qdrant, settings, tenant_id, document_id) -> int:
    points, _ = await qdrant.scroll(
        collection_name=settings.qdrant_collection,
        scroll_filter=build_search_filter(tenant_id=tenant_id, document_ids=[document_id]),
        limit=100,
        with_payload=False,
        with_vectors=False,
    )
    return len(points)


async def test_deleting_a_document_removes_its_evidence(qdrant, settings, two_tenants):
    tenant_id, _ = two_tenants
    document_id = await _document(tenant_id)
    await _index(qdrant, settings, tenant_id, document_id)
    assert await _chunk_count(qdrant, settings, tenant_id, document_id) == 3

    # The same client the API hands over from `app.state`, so the purge is
    # exercised against the store the chunks actually live in.
    await DocumentService(settings).delete(
        tenant_id=tenant_id, document_id=document_id, qdrant=qdrant
    )

    assert await _chunk_count(qdrant, settings, tenant_id, document_id) == 0

    async with tenant_session(tenant_id) as session:
        assert await DocumentRepository(session).get(document_id) is None
        rows, total = await DocumentRepository(session).list()
    assert total == 0, "and it leaves the customer's document list"


async def test_deleting_another_tenants_document_is_not_found(qdrant, settings, two_tenants):
    """404, not 403 — under RLS the row is genuinely invisible, and confirming
    it exists would make this an enumeration oracle."""
    a, b = two_tenants
    document_id = await _document(a)

    with pytest.raises(TenantMismatchError):
        await DocumentService(settings).delete(tenant_id=b, document_id=document_id, qdrant=qdrant)

    # The other tenant's row survives the failed attempt.
    async with tenant_session(a) as session:
        assert await DocumentRepository(session).get(document_id) is not None
