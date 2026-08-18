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
from fastapi.middleware.cors import CORSMiddleware

from app.api.middleware.error_handler import (
    ExceptionToResponseMiddleware,
    register_exception_handlers,
)
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
    # Order matters. `add_middleware` prepends, so the LAST call is the
    # OUTERMOST layer. Registering the exception middleware first and CORS
    # second yields:  CORS -> exception catcher -> routes.
    #
    # That nesting is what guarantees every response carries CORS headers,
    # including 500s. Starlette's own catch-all sits outside CORS, so relying
    # on it makes server errors arrive in the browser as opaque CORS failures.
    app.add_middleware(ExceptionToResponseMiddleware)
    _configure_cors(app, settings)

    register_exception_handlers(app)
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(generation.router, prefix="/api/v1")
    return app


def _configure_cors(app: FastAPI, settings) -> None:
    """Allow the Next.js frontend to call this API from the browser.

    Three things here are load-bearing:

    * **Explicit origins, never ``["*"]``.** The frontend sends credentials, and
      the CORS spec forbids pairing a wildcard origin with
      ``Access-Control-Allow-Credentials: true``. Starlette will configure it
      without complaint and every browser will then reject the response, which
      surfaces as an opaque network error rather than anything mentioning CORS.

    * **``allow_credentials=True``.** The access token is an httpOnly cookie, so
      it is never readable by JS and cannot be attached as a header. Without
      this flag the browser strips the cookie and every request is a 401.

    * **``expose_headers``.** Response headers are invisible to JS unless named
      here. ``X-Request-ID`` is what makes a user-reported failure traceable to
      a server log line.
    """
    if not settings.cors_origins:
        logger.warning("CORS_ORIGINS is empty; browser clients will be blocked")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        # Explicit rather than "*": the upload endpoint is a multipart POST that
        # triggers a preflight, and OPTIONS must be answered for it.
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        # Cache the preflight for 10 minutes. Without it the browser preflights
        # every single generate-answer call, doubling request count on the
        # busiest path in the product.
        max_age=600,
    )
    logger.info("CORS enabled for %s", settings.cors_origins)


app = create_app()
