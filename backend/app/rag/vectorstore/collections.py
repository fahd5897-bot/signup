"""Qdrant collection provisioning and lifecycle.

Idempotent by design: :meth:`QdrantCollectionManager.initialise` is safe to run
on every application boot and from CI. It creates the collection if missing,
always reconciles payload indexes (they can be added to a populated collection
without a rebuild), and refuses to silently continue if an existing collection
disagrees with the configured embedding dimension.
"""

from __future__ import annotations

import logging
from typing import Final

from qdrant_client import AsyncQdrantClient, models

from app.core.config import Settings, get_settings
from app.rag.vectorstore.schema import PayloadField, VectorName

logger = logging.getLogger(__name__)

#: Payload indexes to reconcile on every boot.
#:
#: ``tenant_id`` is the critical one. ``is_tenant=True`` tells Qdrant this field
#: partitions the data, so it physically groups each tenant's vectors on disk.
#: The effect is that a tenant-filtered search reads one contiguous region
#: instead of walking a global HNSW graph and discarding other tenants' hits —
#: isolation and performance from the same declaration.
_PAYLOAD_INDEXES: Final[list[tuple[str, models.PayloadSchemaType | models.KeywordIndexParams]]] = [
    (
        PayloadField.TENANT_ID,
        models.KeywordIndexParams(
            type=models.KeywordIndexType.KEYWORD,
            is_tenant=True,  # ← physical partitioning; see docstring above
            on_disk=True,
        ),
    ),
    (PayloadField.WORKSPACE_ID, models.PayloadSchemaType.KEYWORD),
    (PayloadField.DOCUMENT_ID, models.PayloadSchemaType.KEYWORD),
    (PayloadField.DOCUMENT_ROLE, models.PayloadSchemaType.KEYWORD),
    (PayloadField.CHUNK_TYPE, models.PayloadSchemaType.KEYWORD),
    (PayloadField.LANGUAGE, models.PayloadSchemaType.KEYWORD),
    # Integer so retrieval can range-filter to a page window when a reviewer
    # asks "answer this from pages 40-60 of the specification".
    (PayloadField.PAGE_NUMBER, models.PayloadSchemaType.INTEGER),
]


class QdrantCollectionManager:
    """Owns the collection's shape. Nothing else may create or alter it."""

    def __init__(self, client: AsyncQdrantClient, settings: Settings | None = None) -> None:
        self._client = client
        self._settings = settings or get_settings()
        self._collection = self._settings.qdrant_collection

    # ------------------------------------------------------------------ setup
    async def initialise(self) -> None:
        """Create the collection if absent, then reconcile payload indexes."""
        if await self._client.collection_exists(self._collection):
            logger.info("qdrant collection %s exists; verifying", self._collection)
            await self._verify_compatible()
        else:
            logger.info("creating qdrant collection %s", self._collection)
            await self._create_collection()

        # Always reconciled: indexes may be added to a populated collection, so
        # a newly introduced filter field does not require a reindex.
        await self._ensure_payload_indexes()

    async def _create_collection(self) -> None:
        """Create the collection with named dense + sparse vectors.

        One collection for all tenants, not one per tenant. Per-tenant
        collections exhaust file handles, multiply HNSW memory overhead, and
        turn any reindex into O(tenants) work. Partitioning via the
        ``is_tenant`` payload index gives the same locality without any of that.
        """
        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config={
                VectorName.DENSE: models.VectorParams(
                    size=self._settings.embedding_dim,
                    distance=models.Distance.COSINE,
                    #: Vectors live on disk; the HNSW graph stays in RAM. The
                    #: right trade for a corpus that grows without bound while
                    #: query volume stays modest.
                    on_disk=True,
                    hnsw_config=models.HnswConfigDiff(
                        m=16,
                        ef_construct=128,
                        #: payload_m enables the per-tenant subgraphs that make
                        #: the tenant index a performance win rather than just
                        #: a filter.
                        payload_m=16,
                    ),
                    #: int8 scalar quantisation: ~4x less memory, negligible
                    #: recall loss at this dimensionality. ``always_ram`` keeps
                    #: the quantised vectors resident so rescoring stays fast.
                    quantization_config=models.ScalarQuantization(
                        scalar=models.ScalarQuantizationConfig(
                            type=models.ScalarType.INT8,
                            quantile=0.99,
                            always_ram=True,
                        )
                    ),
                )
            },
            sparse_vectors_config={
                VectorName.SPARSE: models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=True),
                    #: IDF weighting is applied server-side. Without it, common
                    #: Arabic function words dominate the sparse score and
                    #: lexical matching becomes useless for exactly the rare
                    #: terms — reference numbers, standards — it exists to find.
                    modifier=models.Modifier.IDF,
                )
            },
            #: Payload on disk: chunk text is large and only needed for the
            #: handful of points that survive reranking.
            on_disk_payload=True,
            optimizers_config=models.OptimizersConfigDiff(
                #: Deferred indexing during bulk ingest. A 400-page tender
                #: upserts thousands of points at once; building HNSW per batch
                #: would dominate ingestion time.
                indexing_threshold=20_000,
            ),
        )

    async def _ensure_payload_indexes(self) -> None:
        """Create any missing payload index. Existing ones are left untouched."""
        info = await self._client.get_collection(self._collection)
        existing = set(info.payload_schema or {})

        for field_name, field_schema in _PAYLOAD_INDEXES:
            if field_name in existing:
                continue
            logger.info("creating payload index %s.%s", self._collection, field_name)
            await self._client.create_payload_index(
                collection_name=self._collection,
                field_name=field_name,
                field_schema=field_schema,
                wait=True,
            )

    async def _verify_compatible(self) -> None:
        """Fail fast when the live collection disagrees with configuration.

        Changing ``EMBEDDING_MODEL`` without reindexing is the failure this
        catches. Qdrant would reject writes of the wrong width, but a *query*
        vector of the wrong width against the right-width collection returns
        confidently wrong neighbours — which in this product means confidently
        wrong citations. Refusing to boot is the correct response.
        """
        info = await self._client.get_collection(self._collection)
        vectors = info.config.params.vectors

        if not isinstance(vectors, dict) or VectorName.DENSE not in vectors:
            raise RuntimeError(
                f"collection {self._collection!r} has no named vector "
                f"{VectorName.DENSE!r}; it predates hybrid retrieval and must "
                "be recreated"
            )

        actual = vectors[VectorName.DENSE].size
        expected = self._settings.embedding_dim
        if actual != expected:
            raise RuntimeError(
                f"embedding dimension mismatch for {self._collection!r}: "
                f"collection has {actual}, settings expect {expected} "
                f"(model {self._settings.embedding_model!r}). Reindex is required."
            )

        sparse = info.config.params.sparse_vectors or {}
        if VectorName.SPARSE not in sparse:
            logger.warning(
                "collection %s lacks the %r sparse vector; retrieval will fall "
                "back to dense-only and lexical matching on reference numbers "
                "will degrade",
                self._collection,
                VectorName.SPARSE,
            )

    # ------------------------------------------------------------- maintenance
    async def delete_tenant_data(self, tenant_id: str) -> None:
        """Erase every vector belonging to a tenant.

        Required for GDPR/PDPL erasure and for tenant offboarding. Deletion is
        by filter rather than by ID list because the caller does not, and should
        not need to, know every point ID a tenant owns.
        """
        await self._client.delete(
            collection_name=self._collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key=PayloadField.TENANT_ID,
                            match=models.MatchValue(value=tenant_id),
                        )
                    ]
                )
            ),
            wait=True,
        )
        logger.info("deleted all vectors for tenant %s", tenant_id)

    async def delete_document_data(self, tenant_id: str, document_id: str) -> None:
        """Remove one document's chunks — on re-upload or on document deletion.

        The tenant condition is included even though ``document_id`` is already
        unique. Defence in depth: a malformed or attacker-supplied document ID
        must not be able to delete another tenant's points.
        """
        await self._client.delete(
            collection_name=self._collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key=PayloadField.TENANT_ID,
                            match=models.MatchValue(value=tenant_id),
                        ),
                        models.FieldCondition(
                            key=PayloadField.DOCUMENT_ID,
                            match=models.MatchValue(value=document_id),
                        ),
                    ]
                )
            ),
            wait=True,
        )

    async def health(self) -> dict[str, object]:
        """Readiness-probe projection."""
        info = await self._client.get_collection(self._collection)
        return {
            "collection": self._collection,
            "status": info.status,
            "points_count": info.points_count,
            "indexed_vectors_count": info.indexed_vectors_count,
            "payload_indexes": sorted(info.payload_schema or {}),
        }


async def build_client(settings: Settings | None = None) -> AsyncQdrantClient:
    """Construct the shared async client. Held for the app's lifetime."""
    settings = settings or get_settings()
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
        timeout=settings.qdrant_timeout,
        prefer_grpc=True,  # materially faster for bulk upserts
    )
