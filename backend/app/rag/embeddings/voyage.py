"""Voyage AI embeddings — the default provider.

Chosen for Arabic/English parity and for placing cross-lingual paraphrases near
each other, which is what makes an Arabic tender requirement retrieve an English
past proposal.
"""

from __future__ import annotations

import asyncio
import logging

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import Settings, get_settings
from app.core.exceptions import EmbeddingError
from app.rag.embeddings.base import EmbeddingProvider, EmbeddingResult

logger = logging.getLogger(__name__)

#: Output width of voyage-multilingual-2.
_VOYAGE_DIMENSION = 1024
#: Provider batch ceiling.
_MAX_BATCH = 128


class VoyageEmbeddingProvider(EmbeddingProvider):
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.model_name = self._settings.embedding_model
        self.dimension = self._settings.embedding_dim
        self.max_batch_size = _MAX_BATCH
        self._client = None

        if self.dimension != _VOYAGE_DIMENSION:
            logger.warning(
                "configured embedding_dim=%d differs from %s's native %d",
                self.dimension,
                self.model_name,
                _VOYAGE_DIMENSION,
            )

    def _get_client(self):
        if self._client is None:
            try:
                import voyageai
            except ImportError as exc:  # pragma: no cover
                raise EmbeddingError(f"voyageai is not installed: {exc}") from exc
            self._client = voyageai.Client(api_key=self._settings.voyage_api_key.get_secret_value())
        return self._client

    @retry(
        retry=retry_if_exception_type(EmbeddingError),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def _embed(self, texts: list[str], input_type: str) -> EmbeddingResult:
        client = self._get_client()
        try:
            # The SDK is synchronous; run it off the event loop so one slow
            # embedding call cannot stall the whole worker.
            response = await asyncio.to_thread(
                client.embed, texts, model=self.model_name, input_type=input_type
            )
        except Exception as exc:
            raise EmbeddingError(f"voyage embedding failed: {exc}") from exc

        return EmbeddingResult(
            vectors=response.embeddings,
            model=self.model_name,
            total_tokens=getattr(response, "total_tokens", None),
        )

    async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], model=self.model_name)

        vectors: list[list[float]] = []
        tokens = 0
        for start in range(0, len(texts), self.max_batch_size):
            batch = texts[start : start + self.max_batch_size]
            result = await self._embed(batch, input_type="document")
            vectors.extend(result.vectors)
            tokens += result.total_tokens or 0

        if len(vectors) != len(texts):
            # Would silently misalign every vector with the wrong chunk, so
            # every citation would point at the wrong source. Fail loudly.
            raise EmbeddingError(
                f"provider returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        return EmbeddingResult(vectors=vectors, model=self.model_name, total_tokens=tokens)

    async def embed_query(self, text: str) -> list[float]:
        result = await self._embed([text], input_type="query")
        return result.vectors[0]
