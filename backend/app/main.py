"""FastAPI application factory.

Long-lived clients (Qdrant, Anthropic) are created once in the lifespan and
held on ``app.state``. Constructing them per request would open a new connection
pool on every question and discard the Anthropic client's retry state.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anthropic import AsyncAnthropic
from fastapi import FastAPI

from app.api.middleware.error_handler import register_exception_handlers
from app.api.v1.routers import documents, generation
from app.core.config import get_settings
from app.rag.vectorstore.collections import QdrantCollectionManager, build_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    app.state.qdrant = await build_client(settings)
    # Verifies the collection's shape and refuses to start on an embedding
    # dimension mismatch — which would otherwise return confidently wrong
    # neighbours, and therefore wrong citations, at query time.
    await QdrantCollectionManager(app.state.qdrant, settings).initialise()

    app.state.anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    logger.info(
        "started in %s: generation=%s collection=%s",
        settings.environment,
        settings.llm_model_generation,
        settings.qdrant_collection,
    )
    try:
        yield
    finally:
        await app.state.qdrant.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="RFP Response Platform",
        version="0.1.0",
        docs_url="/docs" if settings.environment != "production" else None,
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(generation.router, prefix="/api/v1")
    return app


app = create_app()
