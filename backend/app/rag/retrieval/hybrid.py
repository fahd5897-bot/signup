"""Hybrid retrieval: dense + sparse, fused, tenant-scoped.

Dense embeddings find meaning ("we hold ISO 27001" for "information-security
certification"); sparse BM25 finds literal tokens (``MOH/2026/IT/0114``,
``ISO 27001``, an Arabic term of art). Tender questions need both, and either
alone loses answers the other would have found.

Fusion is done server-side by Qdrant with Reciprocal Rank Fusion, so the two
result sets are combined in one round trip rather than merged in Python.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.core.config import Settings, get_settings
from app.core.exceptions import VectorStoreError
from app.db.models.enums import ChunkType, Language
from app.rag.embeddings import get_embedding_provider, get_sparse_encoder
from app.rag.vectorstore.filters import build_search_filter
from app.rag.vectorstore.schema import PayloadField, VectorName

logger = logging.getLogger(__name__)

#: Candidates pulled from each arm before fusion. Wider than the final K
#: because rerankers only help if given something to choose between.
_CANDIDATES_PER_ARM = 50
DEFAULT_TOP_K = 8


@dataclass(slots=True)
class RetrievedChunk:
    """One candidate passage, with everything needed to cite it."""

    chunk_id: str
    text: str
    raw_text: str
    document_id: uuid.UUID
    document_name: str
    page_number: int | None
    chunk_type: ChunkType
    section_path: str | None
    language: Language
    #: Fusion score from Qdrant, or the reranker score once reranked.
    score: float
    tenant_id: str
    bbox: list[float] | None = None

    @property
    def citation_title(self) -> str:
        """Human-readable label shown on the citation chip in the UI."""
        if self.page_number is not None:
            return f"{self.document_name} — p.{self.page_number}"
        return self.document_name

    @property
    def citation_source(self) -> str:
        """Stable identifier handed to the API as the search result's source.

        The Qdrant point ID, so a returned citation can be resolved back to the
        exact chunk it came from — and checked against the caller's tenant. A
        citation naming a source that is not in the set we supplied is
        rejected outright.
        """
        return f"qdrant://{self.chunk_id}"


async def retrieve(
    *,
    query: str,
    tenant_id: uuid.UUID | str,
    qdrant: AsyncQdrantClient,
    workspace_id: uuid.UUID | str | None = None,
    top_k: int = DEFAULT_TOP_K,
    document_ids: list[uuid.UUID | str] | None = None,
    chunk_types: list[ChunkType] | None = None,
    languages: list[Language] | None = None,
    settings: Settings | None = None,
) -> list[RetrievedChunk]:
    """Retrieve the top-K chunks for one question, scoped to one tenant.

    ``tenant_id`` is keyword-only and mandatory. The filter is built by
    :func:`app.rag.vectorstore.filters.build_search_filter`, which is the only
    place in the codebase permitted to construct a Qdrant filter — so tenant
    isolation is reviewable in one file rather than at every call site.

    Args:
        query: The RFP question, verbatim.
        tenant_id: Owning tenant. Always applied as a hard filter.
        workspace_id: Adds this pursuit's own documents to the tenant-wide
            knowledge base. Omitting it searches global knowledge only.
        top_k: Chunks returned after fusion.

    Returns:
        Chunks ordered best-first. Empty when nothing matched, which the
        caller must treat as grounds to abstain rather than to guess.
    """
    settings = settings or get_settings()
    if not query.strip():
        return []

    search_filter = build_search_filter(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        document_ids=document_ids,
        chunk_types=chunk_types,
        languages=languages,
    )

    provider = get_embedding_provider(settings)
    dense_vector = await provider.embed_query(query)
    sparse_indices, sparse_values = get_sparse_encoder().encode_query(query)

    try:
        response = await qdrant.query_points(
            collection_name=settings.qdrant_collection,
            # Each prefetch is one retrieval arm; the outer query fuses them.
            prefetch=[
                models.Prefetch(
                    query=dense_vector,
                    using=VectorName.DENSE,
                    filter=search_filter,
                    limit=_CANDIDATES_PER_ARM,
                ),
                models.Prefetch(
                    query=models.SparseVector(indices=sparse_indices, values=sparse_values),
                    using=VectorName.SPARSE,
                    filter=search_filter,
                    limit=_CANDIDATES_PER_ARM,
                ),
            ],
            # RRF over DBSF: it combines ranks rather than raw scores, so a
            # cosine similarity and a BM25 weight — which are not on remotely
            # the same scale — can be merged without normalising either.
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            # Belt and braces: the prefetches are already filtered, but a
            # future edit that drops one of those must not widen the result set.
            query_filter=search_filter,
            limit=top_k,
            with_payload=True,
        )
    except Exception as exc:
        raise VectorStoreError(f"retrieval failed: {exc}") from exc

    chunks = [_to_chunk(point) for point in response.points]

    # Isolation assertion, not an optimisation. If a filter regression ever let
    # another tenant's chunk through, it must fail loudly here rather than
    # become a citation in a customer's tender submission.
    expected = str(tenant_id)
    leaked = [c for c in chunks if c.tenant_id != expected]
    if leaked:
        logger.critical(
            "tenant isolation breach: query for %s returned chunks owned by %s",
            expected,
            {c.tenant_id for c in leaked},
        )
        raise VectorStoreError("retrieval returned chunks from another tenant")

    logger.info(
        "retrieved %d chunks for tenant %s (top score %.3f)",
        len(chunks),
        expected,
        chunks[0].score if chunks else 0.0,
    )
    return chunks


def _to_chunk(point: Any) -> RetrievedChunk:
    payload: dict[str, Any] = point.payload or {}
    return RetrievedChunk(
        chunk_id=str(point.id),
        text=payload.get(PayloadField.TEXT, ""),
        # Falls back to the normalised text only if raw is somehow absent;
        # citations must quote the original, never the dediacritised form.
        raw_text=payload.get(PayloadField.RAW_TEXT) or payload.get(PayloadField.TEXT, ""),
        document_id=uuid.UUID(payload[PayloadField.DOCUMENT_ID]),
        document_name=payload.get(PayloadField.DOCUMENT_NAME, "untitled"),
        page_number=payload.get(PayloadField.PAGE_NUMBER),
        chunk_type=ChunkType(payload.get(PayloadField.CHUNK_TYPE, ChunkType.NARRATIVE.value)),
        section_path=payload.get(PayloadField.SECTION_PATH),
        language=Language(payload.get(PayloadField.LANGUAGE, Language.UNKNOWN.value)),
        score=float(point.score or 0.0),
        tenant_id=payload.get(PayloadField.TENANT_ID, ""),
        bbox=payload.get(PayloadField.BBOX),
    )
