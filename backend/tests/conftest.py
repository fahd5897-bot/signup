"""Shared test fixtures."""

from __future__ import annotations

import os

import pytest

# Settings are validated at import time, so the environment must be populated
# before any `app.*` module is imported.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")
os.environ.setdefault("VOYAGE_API_KEY", "pa-test")
os.environ.setdefault("POSTGRES_DSN", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault(
    "JWT_SECRET",
    # Must clear the 32-byte floor enforced by Settings; a fixed value keeps
    # token fixtures reproducible across runs.
    "test-secret-not-for-production-0123456789abcdef",
)

from app.core.config import get_settings  # noqa: E402
from app.rag.vectorstore import QdrantCollectionManager  # noqa: E402
from qdrant_client import AsyncQdrantClient  # noqa: E402


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
async def qdrant(settings):
    """In-memory Qdrant with the production collection shape applied.

    Note: local mode ignores payload indexes, so index *behaviour* (tenant
    partitioning, performance) needs a real server — mark those tests with
    `@pytest.mark.integration` and run them against testcontainers. Filter
    semantics, which is what these tests cover, are identical in both modes.
    """
    client = AsyncQdrantClient(":memory:")
    await QdrantCollectionManager(client, settings).initialise()
    yield client
    await client.close()
