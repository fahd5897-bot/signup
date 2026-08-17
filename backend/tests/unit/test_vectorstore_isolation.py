"""Tenant and workspace isolation in the vector store.

These are the highest-value tests in the suite: a regression here leaks one
customer's confidential tender material into another's generated proposal.
"""

from __future__ import annotations

import uuid

import pytest
from app.rag.vectorstore import (
    ChunkPayload,
    TenantScopeError,
    VectorName,
    build_search_filter,
)
from qdrant_client import models


def _point(
    settings, *, tenant: str, name: str, workspace: str | None = None, role: str = "knowledge_base"
) -> models.PointStruct:
    payload = ChunkPayload(
        tenant_id=tenant,
        workspace_id=workspace,
        document_id=str(uuid.uuid4()),
        document_name=name,
        document_role=role,
        chunk_type="narrative",
        chunk_index=0,
        text="iso 27001 certified",
        raw_text="ISO 27001 Certified",
        language="en",
    )
    return models.PointStruct(
        id=str(uuid.uuid4()),
        vector={
            VectorName.DENSE: [0.1] * settings.embedding_dim,
            VectorName.SPARSE: models.SparseVector(indices=[1], values=[1.0]),
        },
        payload=payload.to_qdrant(),
    )


async def _search(client, settings, **filter_kwargs) -> list[str]:
    result = await client.query_points(
        collection_name=settings.qdrant_collection,
        query=[0.1] * settings.embedding_dim,
        using=VectorName.DENSE,
        query_filter=build_search_filter(**filter_kwargs),
        limit=50,
        with_payload=True,
    )
    return sorted(p.payload["document_name"] for p in result.points)


async def test_tenant_filter_excludes_other_tenants(qdrant, settings):
    await qdrant.upsert(
        settings.qdrant_collection,
        points=[
            _point(settings, tenant="tenant-a", name="a.pdf"),
            _point(settings, tenant="tenant-b", name="b.pdf"),
        ],
        wait=True,
    )
    assert await _search(qdrant, settings, tenant_id="tenant-a") == ["a.pdf"]


async def test_workspace_scope_includes_global_knowledge(qdrant, settings):
    """Regression guard.

    Encoding "tenant-wide" as an absent key plus an IsNull filter silently
    hides global knowledge from every workspace search — retrieval still
    returns the workspace's own documents, so it looks like weak recall rather
    than a bug. The GLOBAL_WORKSPACE sentinel exists to prevent that.
    """
    await qdrant.upsert(
        settings.qdrant_collection,
        points=[
            _point(settings, tenant="t1", name="global.pdf", workspace=None),
            _point(settings, tenant="t1", name="ws1.pdf", workspace="ws-1"),
            _point(settings, tenant="t1", name="ws2.pdf", workspace="ws-2"),
        ],
        wait=True,
    )
    found = await _search(qdrant, settings, tenant_id="t1", workspace_id="ws-1")
    assert found == ["global.pdf", "ws1.pdf"]


async def test_tender_documents_excluded_by_default(qdrant, settings):
    """A tender is the question, never the evidence."""
    await qdrant.upsert(
        settings.qdrant_collection,
        points=[
            _point(settings, tenant="t1", name="kb.pdf"),
            _point(settings, tenant="t1", name="tender.pdf", role="tender"),
        ],
        wait=True,
    )
    assert await _search(qdrant, settings, tenant_id="t1") == ["kb.pdf"]
    assert await _search(qdrant, settings, tenant_id="t1", include_tender_documents=True) == [
        "kb.pdf",
        "tender.pdf",
    ]


def test_filter_requires_tenant_id():
    with pytest.raises(TypeError):
        build_search_filter(workspace_id="ws-1")  # type: ignore[call-arg]
    with pytest.raises(TenantScopeError):
        build_search_filter(tenant_id="")


async def test_delete_tenant_data_is_scoped(qdrant, settings):
    from app.rag.vectorstore import QdrantCollectionManager

    await qdrant.upsert(
        settings.qdrant_collection,
        points=[
            _point(settings, tenant="t1", name="a.pdf"),
            _point(settings, tenant="t2", name="b.pdf"),
        ],
        wait=True,
    )
    await QdrantCollectionManager(qdrant, settings).delete_tenant_data("t1")
    assert await _search(qdrant, settings, tenant_id="t1") == []
    assert await _search(qdrant, settings, tenant_id="t2") == ["b.pdf"]
