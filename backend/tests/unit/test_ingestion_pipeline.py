"""End-to-end ingestion against a real Qdrant, with embeddings stubbed.

Embeddings are the only stubbed component — the parser, chunker, normalisers,
payload construction, and vector store all run for real, because those are where
the failures that matter actually occur.
"""

from __future__ import annotations

import uuid

import app.ingestion.pipeline as pipeline_module
import pytest
from app.db.models.enums import DocumentRole, DocumentStatus
from app.ingestion.pipeline import IngestionPipeline
from app.rag.embeddings.base import EmbeddingProvider, EmbeddingResult
from app.rag.vectorstore import VectorName, build_search_filter
from qdrant_client import AsyncQdrantClient

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def arabic_xlsx(tmp_path) -> bytes:
    """A bilingual bill of quantities, as a real .xlsx file."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "BoQ"
    sheet.append(["البند", "الوصف", "الكمية", "السعر"])
    sheet.append([1, "توريد وتركيب كابلات", 250, "1,250.00"])
    sheet.append([2, "أعمال الصيانة", 12, "9,800.00"])
    summary = workbook.create_sheet("Summary")
    summary.append(["Total", "11,050.00"])

    path = tmp_path / "boq.xlsx"
    workbook.save(path)
    return path.read_bytes()


@pytest.fixture
def stub_embeddings(settings, monkeypatch):
    class _Stub(EmbeddingProvider):
        dimension = settings.embedding_dim
        model_name = "stub"
        max_batch_size = 128

        async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
            return EmbeddingResult([[0.1] * self.dimension for _ in texts], self.model_name, 1)

        async def embed_query(self, text: str) -> list[float]:
            return [0.1] * self.dimension

    class _Sparse:
        def encode_documents(self, texts: list[str]):
            return [([1, 7], [0.9, 0.3]) for _ in texts]

    monkeypatch.setattr(pipeline_module, "get_embedding_provider", lambda *a, **k: _Stub())
    monkeypatch.setattr(pipeline_module, "get_sparse_encoder", lambda: _Sparse())


async def test_ingests_arabic_spreadsheet_end_to_end(
    qdrant: AsyncQdrantClient, settings, arabic_xlsx, stub_embeddings
) -> None:
    tenant_id, document_id = uuid.uuid4(), uuid.uuid4()
    transitions: list[DocumentStatus] = []

    async def track(_doc_id, status):
        transitions.append(status)

    result = await IngestionPipeline(qdrant, settings, status_callback=track).run(
        data=arabic_xlsx,
        document_id=document_id,
        tenant_id=tenant_id,
        filename="كراسة الشروط.xlsx",
        mime_type=XLSX_MIME,
        role=DocumentRole.KNOWLEDGE_BASE,
    )

    assert result.succeeded, result.failure_reason
    assert result.chunk_count == 2  # one per sheet
    assert result.table_count == 2
    assert transitions == [
        DocumentStatus.PARSING,
        DocumentStatus.CHUNKING,
        DocumentStatus.EMBEDDING,
        DocumentStatus.READY,
    ]

    found = await qdrant.query_points(
        collection_name=settings.qdrant_collection,
        query=[0.1] * settings.embedding_dim,
        using=VectorName.DENSE,
        query_filter=build_search_filter(tenant_id=str(tenant_id)),
        limit=10,
        with_payload=True,
    )
    assert len(found.points) == 2
    payloads = [p.payload for p in found.points]
    # Table markup must survive into the payload; flattened text loses which
    # price belongs to which line item.
    assert all(p["raw_text"].startswith("<table>") for p in payloads)
    assert all(p["tenant_id"] == str(tenant_id) for p in payloads)
    assert all(p["chunk_type"] == "table" for p in payloads)


async def test_reingestion_replaces_rather_than_duplicates(
    qdrant: AsyncQdrantClient, settings, arabic_xlsx, stub_embeddings
) -> None:
    """Stale chunks are worse than missing ones: they remain citable evidence
    for text that no longer exists in the document.
    """
    tenant_id, document_id = uuid.uuid4(), uuid.uuid4()
    pipeline = IngestionPipeline(qdrant, settings)
    kwargs = dict(
        data=arabic_xlsx,
        document_id=document_id,
        tenant_id=tenant_id,
        filename="boq.xlsx",
        mime_type=XLSX_MIME,
        role=DocumentRole.KNOWLEDGE_BASE,
    )

    first = await pipeline.run(**kwargs)
    second = await pipeline.run(**kwargs)
    assert first.succeeded and second.succeeded

    found = await qdrant.query_points(
        collection_name=settings.qdrant_collection,
        query=[0.1] * settings.embedding_dim,
        using=VectorName.DENSE,
        query_filter=build_search_filter(tenant_id=str(tenant_id)),
        limit=50,
    )
    assert len(found.points) == first.chunk_count


@pytest.mark.parametrize(
    ("label", "data", "mime_type", "expected_slug", "retryable"),
    [
        ("empty", b"", "application/pdf", "corrupt_file", False),
        (
            "corrupt pdf",
            b"%PDF-1.4 garbage" + b"\x00" * 80,
            "application/pdf",
            "corrupt_file",
            False,
        ),
        (
            "wrong bytes for type",
            b"plain text, not a spreadsheet",
            XLSX_MIME,
            "no_text_extracted",
            False,
        ),
        (
            "unknown format",
            b"MZ\x90\x00" * 40,
            "application/x-msdownload",
            "unsupported_format",
            False,
        ),
    ],
)
async def test_failures_are_classified_and_never_raise(
    qdrant, settings, stub_embeddings, label, data, mime_type, expected_slug, retryable
) -> None:
    """The pipeline returns failures instead of raising.

    An exception escaping would leave the document in a transient status
    forever, which in the UI is indistinguishable from "still processing".
    """
    result = await IngestionPipeline(qdrant, settings).run(
        data=data,
        document_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        filename=f"{label}.bin",
        mime_type=mime_type,
        role=DocumentRole.KNOWLEDGE_BASE,
    )
    assert result.status is DocumentStatus.FAILED
    assert result.failure_slug == expected_slug
    assert result.is_retryable is retryable
    assert result.failure_reason
