"""Sparse (lexical) vectors for hybrid retrieval.

Dense embeddings are strong on meaning and weak on exact tokens. Tenders are
full of exact tokens that must match literally — reference numbers like
``MOH/2026/IT/0114``, standards like ``ISO 27001``, part codes, and Arabic legal
terms of art. A sparse vector is what actually retrieves those.

Uses fastembed's BM25 locally rather than a hosted service: it needs no network
call, and the IDF weighting is applied server-side by Qdrant (see
``Modifier.IDF`` in the collection config), so the corpus statistics stay
current as documents are added without any reindexing here.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: fastembed's BM25 handles Arabic tokenisation acceptably. It does not stem
#: Arabic, which is why the text handed to it is already normalised (alef/ya
#: unified, diacritics stripped) by app.ingestion.normalizers.arabic.
_MODEL_NAME = "Qdrant/bm25"


class SparseEncoder:
    """Lazily-loaded BM25 encoder.

    Loading is deferred because the model is initialised per worker process and
    an eager import would slow every API pod that never encodes anything.
    """

    def __init__(self, model_name: str = _MODEL_NAME) -> None:
        self._model_name = model_name
        self._model: Any = None

    def _get_model(self) -> Any:
        if self._model is None:
            from fastembed import SparseTextEmbedding

            self._model = SparseTextEmbedding(model_name=self._model_name)
            logger.info("loaded sparse encoder %s", self._model_name)
        return self._model

    def encode_documents(self, texts: list[str]) -> list[tuple[list[int], list[float]]]:
        """Return (indices, values) pairs, ready for ``models.SparseVector``."""
        if not texts:
            return []
        model = self._get_model()
        return [
            (embedding.indices.tolist(), embedding.values.tolist())
            for embedding in model.embed(texts)
        ]

    def encode_query(self, text: str) -> tuple[list[int], list[float]]:
        model = self._get_model()
        embedding = next(iter(model.query_embed(text)))
        return embedding.indices.tolist(), embedding.values.tolist()
