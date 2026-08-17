"""Embedding providers.

Obtain a provider through :func:`get_embedding_provider`; do not construct one
directly, so the deployed model stays a configuration decision.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import Settings, get_settings
from app.rag.embeddings.base import EmbeddingProvider, EmbeddingResult
from app.rag.embeddings.sparse import SparseEncoder
from app.rag.embeddings.voyage import VoyageEmbeddingProvider

__all__ = [
    "EmbeddingProvider",
    "EmbeddingResult",
    "SparseEncoder",
    "VoyageEmbeddingProvider",
    "get_embedding_provider",
    "get_sparse_encoder",
]


@lru_cache
def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    """Resolve the configured provider.

    Selection is by model name rather than a separate provider setting, so the
    two cannot drift out of sync.
    """
    settings = settings or get_settings()
    model = settings.embedding_model.lower()

    if model.startswith("voyage"):
        return VoyageEmbeddingProvider(settings)

    raise ValueError(
        f"unsupported embedding model {settings.embedding_model!r}. "
        "Supported: voyage-* (default). See app/rag/embeddings/base.py for why "
        "English-centric models are a poor fit for this corpus."
    )


@lru_cache
def get_sparse_encoder() -> SparseEncoder:
    return SparseEncoder()
