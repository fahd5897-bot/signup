"""Cross-encoder reranking.

Fusion gives a wide, cheap candidate set; the reranker reads the question and
each passage *together* and reorders them. That matters most for the case this
product cares about: a chunk that mentions "ISO 27001" is not evidence that the
bidder *holds* it, and only a cross-encoder reliably tells those apart.

Reranker scores are also better calibrated than fusion ranks, which is why the
abstention gate reads the rerank score rather than the RRF score.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import Settings, get_settings
from app.rag.retrieval.hybrid import RetrievedChunk

logger = logging.getLogger(__name__)


class Reranker:
    """Voyage cross-encoder reranker."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = None

    def _get_client(self):
        if self._client is None:
            import voyageai

            self._client = voyageai.Client(api_key=self._settings.voyage_api_key.get_secret_value())
        return self._client

    async def rerank(
        self, query: str, chunks: list[RetrievedChunk], *, top_k: int = 8
    ) -> list[RetrievedChunk]:
        """Reorder chunks by relevance, returning the best ``top_k``.

        Degrades rather than fails: if the reranker is unavailable, the fused
        order is returned unchanged. A slightly worse ordering still produces a
        grounded, cited answer; a hard failure produces none at all. The
        downgrade is logged because it also weakens the abstention gate's
        calibration.
        """
        if not chunks:
            return []

        # Tables are cited by their markup, which a reranker scores poorly.
        # Rerank on the flattened text and carry the score back to the chunk.
        documents = [c.text for c in chunks]

        try:
            client = self._get_client()
            response = await asyncio.to_thread(
                client.rerank,
                query=query,
                documents=documents,
                model=self._settings.rerank_model,
                top_k=min(top_k, len(documents)),
            )
        except Exception as exc:
            logger.warning(
                "reranker unavailable (%s); falling back to fusion order. "
                "Abstention thresholds are calibrated on rerank scores and are "
                "less reliable in this mode.",
                exc,
            )
            return chunks[:top_k]

        reranked: list[RetrievedChunk] = []
        for result in response.results:
            chunk = chunks[result.index]
            chunk.score = float(result.relevance_score)
            reranked.append(chunk)

        logger.info(
            "reranked %d -> %d chunks (top %.3f)",
            len(chunks),
            len(reranked),
            reranked[0].score if reranked else 0.0,
        )
        return reranked
