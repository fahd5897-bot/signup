"""Embedding provider interface.

Deliberately a narrow protocol with two methods, because the choice of provider
is the single most consequential and most likely-to-change decision in the
retrieval stack, and because query and document embedding are *not* the same
operation for asymmetric models.

**On model choice for this product.** ``text-embedding-3-small`` is a reasonable
default for English-only corpora and a poor one here: it is English-centric, and
Arabic retrieval quality drops sharply against multilingual-trained
alternatives. Worse for a bilingual tender product, it does not place an Arabic
requirement and its English answer near each other in vector space, so an
Arabic clause fails to retrieve the English past-bid text that answers it —
which is the core cross-lingual case this platform exists to serve.

Default is therefore ``voyage-multilingual-2``. ``bge-m3`` is the self-hosted
option where tender rules require in-country processing, and OpenAI is
available for English-only tenants who already have the contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    #: Provider-reported token usage, for cost attribution per tenant.
    total_tokens: int | None = None

    def __len__(self) -> int:
        return len(self.vectors)


class EmbeddingProvider(ABC):
    """Dense embedding backend."""

    #: Vector width. Must match ``Settings.embedding_dim`` and the deployed
    #: Qdrant collection; a mismatch is caught at boot by the collection manager.
    dimension: int
    model_name: str
    #: Provider-side cap on inputs per request.
    max_batch_size: int = 128

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        """Embed corpus text for storage."""

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query.

        Separate from :meth:`embed_documents` because asymmetric models
        (Voyage, BGE, E5) apply a different prefix or pooling to queries.
        Embedding a query with the document path costs real recall, and the
        failure is invisible — results still come back, just worse ones.
        """
