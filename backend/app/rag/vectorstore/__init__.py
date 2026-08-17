"""Qdrant access layer.

Filters come from :mod:`app.rag.vectorstore.filters` and nowhere else — see
that module's docstring for why that rule matters.
"""

from app.rag.vectorstore.collections import QdrantCollectionManager, build_client
from app.rag.vectorstore.filters import (
    TenantScopeError,
    build_search_filter,
    build_tenant_only_filter,
)
from app.rag.vectorstore.schema import (
    GLOBAL_WORKSPACE,
    ChunkPayload,
    PayloadField,
    VectorName,
)

__all__ = [
    "GLOBAL_WORKSPACE",
    "ChunkPayload",
    "PayloadField",
    "QdrantCollectionManager",
    "TenantScopeError",
    "VectorName",
    "build_client",
    "build_search_filter",
    "build_tenant_only_filter",
]
