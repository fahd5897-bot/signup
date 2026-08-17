"""The grounded-answer chain and its guardrails.

Qdrant is real; the Anthropic call is faked so the citation shapes the API
would return can be replayed exactly, including the ones that must be rejected.
Each test names the failure it prevents.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import app.rag.chains.answer_requirement as chain_module
import app.rag.retrieval.hybrid as hybrid_module
import pytest
from app.db.models.enums import ChunkType, GroundingVerdict, Language, ProposalStatus
from app.rag.chains.answer_requirement import AnswerChain
from app.rag.grounding.context_builder import build_search_results
from app.rag.prompts.answer_generation import NOT_FOUND_SENTINEL
from app.rag.retrieval.hybrid import RetrievedChunk
from app.rag.vectorstore.schema import ChunkPayload, VectorName
from qdrant_client import models

TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_TENANT = uuid.UUID("22222222-2222-2222-2222-222222222222")

ISO_TEXT = (
    "The company has held ISO 27001:2022 certification since March 2019, "
    "certificate number IS-649201, issued by BSI."
)


# --------------------------------------------------------------- fake API ---
@dataclass
class FakeCitation:
    cited_text: str
    source: str | None = None
    search_result_index: int | None = None
    type: str = "search_result_location"


@dataclass
class FakeTextBlock:
    text: str
    citations: list[FakeCitation] | None = None
    type: str = "text"


@dataclass
class FakeUsage:
    input_tokens: int = 1200
    output_tokens: int = 90
    cache_read_input_tokens: int = 0


@dataclass
class FakeResponse:
    content: list[FakeTextBlock]
    model: str = "claude-opus-5"
    stop_reason: str = "end_turn"
    usage: FakeUsage = field(default_factory=FakeUsage)


class FakeAnthropic:
    """Records the request and replays a scripted response."""

    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.last_request: dict[str, Any] | None = None
        self.call_count = 0
        self.beta = self  # chain calls client.beta.messages.create
        self.messages = self

    async def create(self, **kwargs: Any) -> FakeResponse:
        self.call_count += 1
        self.last_request = kwargs
        return self._response


# ----------------------------------------------------------------- fixtures --
@pytest.fixture
def stub_embeddings(settings, monkeypatch):
    class _Stub:
        async def embed_query(self, text: str) -> list[float]:
            return [0.1] * settings.embedding_dim

    class _Sparse:
        def encode_query(self, text: str):
            return [1], [1.0]

    monkeypatch.setattr(hybrid_module, "get_embedding_provider", lambda *a, **k: _Stub())
    monkeypatch.setattr(hybrid_module, "get_sparse_encoder", lambda: _Sparse())


@pytest.fixture
def no_rerank(monkeypatch):
    """Bypass the network reranker, preserving fusion order and scores."""

    async def _identity(self, query, chunks, *, top_k=8):
        for i, chunk in enumerate(chunks):
            chunk.score = 0.92 - (i * 0.1)
        return chunks[:top_k]

    monkeypatch.setattr(chain_module.Reranker, "rerank", _identity)


async def _seed(
    qdrant, settings, tenant: uuid.UUID, text: str, name: str = "iso-cert.pdf", page: int = 4
) -> str:
    point_id = str(uuid.uuid4())
    payload = ChunkPayload(
        tenant_id=str(tenant),
        document_id=str(uuid.uuid4()),
        document_name=name,
        document_role="knowledge_base",
        page_number=page,
        chunk_type="narrative",
        chunk_index=0,
        text=text.lower(),
        raw_text=text,
        language="en",
        section_path="4 > 4.1 Certifications",
    )
    await qdrant.upsert(
        settings.qdrant_collection,
        points=[
            models.PointStruct(
                id=point_id,
                vector={
                    VectorName.DENSE: [0.1] * settings.embedding_dim,
                    VectorName.SPARSE: models.SparseVector(indices=[1], values=[1.0]),
                },
                payload=payload.to_qdrant(),
            )
        ],
        wait=True,
    )
    return point_id


# -------------------------------------------------------------------- tests --
async def test_generates_cited_answer(qdrant, settings, stub_embeddings, no_rerank):
    point_id = await _seed(qdrant, settings, TENANT, ISO_TEXT)

    fake = FakeAnthropic(
        FakeResponse(
            content=[
                FakeTextBlock(
                    text=(
                        "The company holds ISO 27001:2022, certificate IS-649201, "
                        "issued by BSI in March 2019."
                    ),
                    citations=[FakeCitation(cited_text=ISO_TEXT, source=f"qdrant://{point_id}")],
                )
            ]
        )
    )

    result = await AnswerChain(qdrant, fake, settings).run(
        requirement_ref="3.2.14",
        requirement_text="The Contractor shall hold ISO 27001 certification.",
        tenant_id=TENANT,
    )

    assert result.status is ProposalStatus.DRAFT  # never auto-approved
    assert result.grounding_verdict is GroundingVerdict.VERIFIED
    assert result.citation_coverage == 1.0
    assert len(result.citations) == 1

    citation = result.citations[0]
    assert citation.chunk_id == point_id
    assert citation.document_name == "iso-cert.pdf"
    assert citation.page_number == 4  # the source reference the task asks for
    assert citation.quoted_text == ISO_TEXT
    assert result.model_id == "claude-opus-5"


async def test_abstains_when_nothing_retrieved(qdrant, settings, stub_embeddings, no_rerank):
    """No evidence means no model call at all — not a hedged answer."""
    fake = FakeAnthropic(FakeResponse(content=[]))

    result = await AnswerChain(qdrant, fake, settings).run(
        requirement_ref="9.9",
        requirement_text="Describe your nuclear decommissioning experience.",
        tenant_id=TENANT,
    )

    assert result.status is ProposalStatus.ABSTAINED
    assert result.answer_text is None  # no placeholder to rubber-stamp
    assert result.abstention_reason
    assert fake.call_count == 0  # tokens not spent


async def test_abstains_below_evidence_threshold(qdrant, settings, stub_embeddings, monkeypatch):
    """A weak best-match must not be generated from.

    This is the dangerous case: the model would produce a fluent answer citing
    a barely-relevant passage, which reads as properly sourced.
    """
    await _seed(qdrant, settings, TENANT, "Our office is in Riyadh.")

    async def _weak(self, query, chunks, *, top_k=8):
        for chunk in chunks:
            chunk.score = 0.11
        return chunks[:top_k]

    monkeypatch.setattr(chain_module.Reranker, "rerank", _weak)
    fake = FakeAnthropic(FakeResponse(content=[]))

    result = await AnswerChain(qdrant, fake, settings).run(
        requirement_ref="3.2.14",
        requirement_text="The Contractor shall hold ISO 27001 certification.",
        tenant_id=TENANT,
    )

    assert result.status is ProposalStatus.ABSTAINED
    assert "threshold" in result.abstention_reason
    assert fake.call_count == 0


async def test_sentinel_is_recognised_verbatim(qdrant, settings, stub_embeddings, no_rerank):
    """The exact refusal string maps to an abstention, not to answer text."""
    await _seed(qdrant, settings, TENANT, ISO_TEXT)
    fake = FakeAnthropic(FakeResponse(content=[FakeTextBlock(text=NOT_FOUND_SENTINEL)]))

    result = await AnswerChain(qdrant, fake, settings).run(
        requirement_ref="3.2.14",
        requirement_text="Do you hold a nuclear operating licence?",
        tenant_id=TENANT,
    )

    assert result.status is ProposalStatus.ABSTAINED
    assert result.answer_text is None
    assert result.model_id == "claude-opus-5"  # the call did happen


async def test_fabricated_citation_is_rejected(qdrant, settings, stub_embeddings, no_rerank):
    """A citation naming a source we never supplied invalidates the answer.

    Offsets are computed server-side against the bytes we sent, so a fabricated
    citation cannot resolve — this asserts we actually act on that.
    """
    await _seed(qdrant, settings, TENANT, ISO_TEXT)

    fake = FakeAnthropic(
        FakeResponse(
            content=[
                FakeTextBlock(
                    text="The company holds ISO 27001 and a nuclear operating licence.",
                    citations=[
                        FakeCitation(
                            cited_text="a nuclear operating licence issued in 2020",
                            source="qdrant://" + str(uuid.uuid4()),  # never supplied
                        )
                    ],
                )
            ]
        )
    )

    result = await AnswerChain(qdrant, fake, settings).run(
        requirement_ref="3.2.14",
        requirement_text="List your certifications.",
        tenant_id=TENANT,
    )

    assert result.grounding_verdict is GroundingVerdict.UNVERIFIED
    assert result.citations == []
    assert result.confidence_score == 0.0


async def test_uncited_claims_lower_coverage(qdrant, settings, stub_embeddings, no_rerank):
    """Sentences the model added beyond its sources must be visible."""
    point_id = await _seed(qdrant, settings, TENANT, ISO_TEXT)

    fake = FakeAnthropic(
        FakeResponse(
            content=[
                FakeTextBlock(
                    text="The company holds ISO 27001:2022, certificate IS-649201.",
                    citations=[FakeCitation(cited_text=ISO_TEXT, source=f"qdrant://{point_id}")],
                ),
                FakeTextBlock(
                    text=" We also maintain a dedicated 24/7 security operations centre in Dubai."
                ),
            ]
        )
    )

    result = await AnswerChain(qdrant, fake, settings).run(
        requirement_ref="3.2.14",
        requirement_text="Describe your security certifications.",
        tenant_id=TENANT,
    )

    assert result.grounding_verdict is GroundingVerdict.PARTIAL
    assert result.citation_coverage < 1.0
    assert any("Dubai" in s for s in result.unsupported_sentences)


async def test_retrieval_is_tenant_scoped(qdrant, settings, stub_embeddings, no_rerank):
    """Another tenant's evidence must never reach the context."""
    await _seed(qdrant, settings, OTHER_TENANT, ISO_TEXT, name="their-cert.pdf")
    fake = FakeAnthropic(FakeResponse(content=[]))

    result = await AnswerChain(qdrant, fake, settings).run(
        requirement_ref="3.2.14",
        requirement_text="The Contractor shall hold ISO 27001 certification.",
        tenant_id=TENANT,
    )

    assert result.status is ProposalStatus.ABSTAINED
    assert fake.call_count == 0


async def test_context_blocks_are_well_formed():
    """Shape must match the search_result contract exactly."""
    chunk = RetrievedChunk(
        chunk_id="abc",
        text="iso 27001",
        raw_text=ISO_TEXT,
        document_id=uuid.uuid4(),
        document_name="cert.pdf",
        page_number=4,
        chunk_type=ChunkType.NARRATIVE,
        section_path="4 > 4.1",
        language=Language.EN,
        score=0.9,
        tenant_id=str(TENANT),
    )
    [block] = build_search_results([chunk])

    assert block["type"] == "search_result"
    assert block["source"] == "qdrant://abc"
    assert block["title"] == "cert.pdf — p.4"
    assert block["citations"] == {"enabled": True}
    assert block["content"][0]["type"] == "text"
    # Citable text must be the original, not the retrieval-normalised form,
    # or the reviewer's highlight will not align with the source PDF.
    assert ISO_TEXT in block["content"][0]["text"]


async def test_tables_keep_html_in_context():
    chunk = RetrievedChunk(
        chunk_id="t1",
        text="item qty price",
        raw_text="<table><tr><td>Item</td><td>Price</td></tr></table>",
        document_id=uuid.uuid4(),
        document_name="boq.xlsx",
        page_number=1,
        chunk_type=ChunkType.TABLE,
        section_path=None,
        language=Language.EN,
        score=0.9,
        tenant_id=str(TENANT),
    )
    [block] = build_search_results([chunk])
    assert "<table>" in block["content"][0]["text"]


async def test_request_shape_sent_to_api(qdrant, settings, stub_embeddings, no_rerank):
    """Guards the model, guardrail prompt, and caching setup."""
    point_id = await _seed(qdrant, settings, TENANT, ISO_TEXT)
    fake = FakeAnthropic(
        FakeResponse(
            content=[
                FakeTextBlock(
                    text="The company holds ISO 27001:2022, certificate IS-649201.",
                    citations=[FakeCitation(cited_text=ISO_TEXT, source=f"qdrant://{point_id}")],
                )
            ]
        )
    )

    await AnswerChain(qdrant, fake, settings).run(
        requirement_ref="3.2.14",
        requirement_text="Do you hold ISO 27001?",
        tenant_id=TENANT,
        language=Language.AR,
    )

    sent = fake.last_request
    assert sent["model"] == settings.llm_model_generation
    assert NOT_FOUND_SENTINEL in sent["system"]  # guardrail is present
    assert "temperature" not in sent  # rejected on Opus 5
    assert sent["thinking"] == {"type": "adaptive"}

    content = sent["messages"][0]["content"]
    results = [b for b in content if b.get("type") == "search_result"]
    assert results and all(b["citations"]["enabled"] for b in results)
    # Cache breakpoint on the last evidence block, so the volatile question
    # stays outside the cached prefix.
    assert results[-1]["cache_control"] == {"type": "ephemeral"}
    assert content[-1]["type"] == "text"


# ------------------------------------------------------- citation mapper ----
def _chunk(chunk_id: str, page: int | None = 4) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text="iso 27001",
        raw_text=ISO_TEXT,
        document_id=uuid.uuid4(),
        document_name=f"{chunk_id}.pdf",
        page_number=page,
        chunk_type=ChunkType.NARRATIVE,
        section_path=None,
        language=Language.EN,
        score=0.9,
        tenant_id=str(TENANT),
    )


def test_citation_with_conflicting_source_and_index_is_refused():
    """When source and index disagree we cannot tell which is right.

    Guessing at attribution in a document submitted to a procurement authority
    is not an acceptable default.
    """
    from app.rag.grounding.citation_mapper import map_citations

    chunks = [_chunk("c0"), _chunk("c1")]
    mapped = map_citations(
        [
            FakeTextBlock(
                text="x",
                citations=[
                    FakeCitation(
                        cited_text="q",
                        source="qdrant://never-supplied",
                        search_result_index=1,
                    )
                ],
            )
        ],
        chunks,
    )
    assert mapped.citations == []
    assert mapped.rejected_count == 1


def test_malformed_page_number_does_not_abort_generation():
    """Regression: page numbers are 1-indexed and Citation enforces it.

    A parser emitting 0 previously raised inside the mapper and took down the
    whole generation. A metadata defect must cost the page label, not the
    evidence.
    """
    from app.rag.grounding.citation_mapper import map_citations

    mapped = map_citations(
        [
            FakeTextBlock(
                text="The company holds ISO 27001.",
                citations=[FakeCitation(cited_text=ISO_TEXT, source="qdrant://c0")],
            )
        ],
        [_chunk("c0", page=0)],
    )
    assert len(mapped.citations) == 1
    assert mapped.citations[0].page_number is None
    assert mapped.rejected_count == 0


def test_repeated_span_yields_one_citation_chip():
    """One passage supporting several sentences is one source, not many."""
    from app.rag.grounding.citation_mapper import map_citations

    mapped = map_citations(
        [
            FakeTextBlock(text="a", citations=[FakeCitation("same", source="qdrant://c0")]),
            FakeTextBlock(text="b", citations=[FakeCitation("same", source="qdrant://c0")]),
        ],
        [_chunk("c0")],
    )
    assert len(mapped.citations) == 1
