"""Turn retrieved chunks into Anthropic ``search_result`` content blocks.

``search_result`` is the API's purpose-built RAG primitive: each block carries a
``source`` and ``title`` you choose, and citations come back naming them. That
is the mechanism behind this product's central claim — the model is never asked
where it found something (which it would invent); the API reports the span it
actually drew on, and the span is checked against the chunks we supplied.

The ``source`` we set is the Qdrant point ID, so every citation resolves to one
specific chunk whose tenant ownership can be verified.
"""

from __future__ import annotations

import logging
from typing import Any

from app.db.models.enums import ChunkType
from app.rag.retrieval.hybrid import RetrievedChunk

logger = logging.getLogger(__name__)

#: Cap on chunks sent in one request. Opus 5 has a 1M window, so this is a cost
#: and precision limit rather than a capacity one: past roughly this many
#: passages, added context dilutes rather than informs, and every extra chunk
#: is another opportunity for a plausible-but-irrelevant citation.
MAX_CONTEXT_CHUNKS = 20


def build_search_results(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    """Build one ``search_result`` block per chunk, in rank order.

    Rank order is preserved because ``search_result_index`` in the returned
    citations indexes into this list — it is the primary key linking a citation
    back to its chunk.
    """
    if len(chunks) > MAX_CONTEXT_CHUNKS:
        logger.info("truncating context from %d to %d chunks", len(chunks), MAX_CONTEXT_CHUNKS)
        chunks = chunks[:MAX_CONTEXT_CHUNKS]

    return [
        {
            "type": "search_result",
            "source": chunk.citation_source,  # qdrant://{point_id}
            "title": chunk.citation_title,  # "spec.pdf — p.14"
            "content": [{"type": "text", "text": _render(chunk)}],
            "citations": {"enabled": True},
        }
        for chunk in chunks
    ]


def _render(chunk: RetrievedChunk) -> str:
    """Render one chunk's citable text.

    ``raw_text`` is used, never the retrieval-normalised form: the model quotes
    what it is given, ``cited_text`` comes back as that quote, and the reviewer
    UI highlights it in the source PDF. Feeding dediacritised Arabic here would
    produce citations that match no glyphs in the actual document.

    Tables keep their HTML. A flattened bill of quantities gives the model no
    way to tell which price belongs to which line item, and a confidently
    mis-attributed unit price in a submitted bid is the exact failure this
    system exists to prevent.
    """
    body = chunk.raw_text

    # A short structural preamble, kept outside the substantive text so it is
    # unlikely to be quoted back as if it were source content.
    header_parts = [chunk.document_name]
    if chunk.section_path:
        header_parts.append(chunk.section_path)
    if chunk.page_number is not None:
        header_parts.append(f"page {chunk.page_number}")
    header = " | ".join(header_parts)

    if chunk.chunk_type is ChunkType.TABLE:
        return f"[{header}]\nThe following is a table in HTML:\n{body}"
    return f"[{header}]\n{body}"
