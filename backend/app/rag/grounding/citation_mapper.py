"""Map API citations onto our ``Citation`` schema, rejecting anything unsound.

This is the enforcement point. The API returns citations naming a ``source``;
this module resolves each one back to the chunk that was actually supplied and
drops any that does not resolve. A citation that cannot be matched to a chunk
in the request's own context set is, by construction, not evidence — and since
the offsets are computed server-side against the bytes we sent, a fabricated
one cannot resolve.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.rag.retrieval.hybrid import RetrievedChunk
from app.schemas.proposal import Citation

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MappedAnswer:
    """An answer split into its cited and uncited spans."""

    text: str
    citations: list[Citation]
    #: Text segments carrying no citation. The verifier decides which of these
    #: are factual claims and therefore problems.
    uncited_segments: list[str]
    #: Citations the API returned that could not be resolved to a supplied
    #: chunk. Non-zero is a hard integrity failure, not a warning.
    rejected_count: int = 0


def map_citations(content_blocks: list[Any], chunks: list[RetrievedChunk]) -> MappedAnswer:
    """Flatten response content into text plus resolved citations.

    Args:
        content_blocks: ``response.content`` from the Messages API.
        chunks: The chunks supplied as search results, in the order supplied.

    Returns:
        The assembled answer with every citation resolved to a real chunk.
    """
    by_source = {chunk.citation_source: chunk for chunk in chunks}

    text_parts: list[str] = []
    citations: list[Citation] = []
    uncited: list[str] = []
    rejected = 0
    seen: set[tuple[str, str]] = set()

    for block in content_blocks:
        if getattr(block, "type", None) != "text":
            continue

        text = block.text or ""
        text_parts.append(text)

        block_citations = getattr(block, "citations", None) or []
        if not block_citations:
            if text.strip():
                uncited.append(text)
            continue

        for raw in block_citations:
            resolved = _resolve(raw, by_source, chunks)
            if resolved is None:
                rejected += 1
                logger.error(
                    "rejected unresolvable citation: source=%r index=%r",
                    getattr(raw, "source", None),
                    getattr(raw, "search_result_index", None),
                )
                continue

            chunk, cited_text = resolved
            # The same passage often supports several sentences; the reviewer
            # wants one chip per source, not one per sentence.
            key = (chunk.chunk_id, cited_text[:120])
            if key in seen:
                continue
            seen.add(key)

            try:
                citations.append(
                    Citation(
                        chunk_id=chunk.chunk_id,
                        document_id=chunk.document_id,
                        document_name=chunk.document_name,
                        # Page numbers are 1-indexed. A parser that emitted 0 or
                        # a negative has a metadata defect, not an evidence
                        # problem: drop the label, keep the citation. Discarding
                        # real evidence over a cosmetic field would be the worse
                        # trade.
                        page_number=_valid_page(chunk.page_number, chunk.chunk_id),
                        chunk_type=chunk.chunk_type,
                        quoted_text=cited_text,
                        section_path=chunk.section_path,
                        relevance_score=_clamp(chunk.score),
                    )
                )
            except ValidationError as exc:
                # Backstop. One malformed payload must not take down the whole
                # generation — the answer is still useful with the remaining
                # citations, and the coverage check will reflect the loss.
                rejected += 1
                logger.error("citation for chunk %s failed validation: %s", chunk.chunk_id, exc)

    return MappedAnswer(
        text="".join(text_parts).strip(),
        citations=citations,
        uncited_segments=uncited,
        rejected_count=rejected,
    )


def _resolve(
    raw: Any, by_source: dict[str, RetrievedChunk], chunks: list[RetrievedChunk]
) -> tuple[RetrievedChunk, str] | None:
    """Resolve one API citation to the chunk it came from.

    Matches on ``source`` first, since that is the identifier we chose and is
    unambiguous. ``search_result_index`` is a fallback for the same reason it is
    not the primary: it is positional, so an off-by-one would silently attribute
    a quote to the neighbouring document.
    """
    cited_text = (getattr(raw, "cited_text", "") or "").strip()
    if not cited_text:
        return None

    source = getattr(raw, "source", None)
    if source and source in by_source:
        return by_source[source], cited_text

    index = getattr(raw, "search_result_index", None)
    if isinstance(index, int) and 0 <= index < len(chunks):
        chunk = chunks[index]
        if source:
            # Index resolved but source did not: the two disagree, and we
            # cannot tell which is right. Refuse rather than guess at
            # attribution in a document that goes to a procurement authority.
            logger.error(
                "citation source %r not in context but index %d resolves to %r",
                source,
                index,
                chunk.citation_source,
            )
            return None
        return chunk, cited_text

    return None


def _valid_page(page: int | None, chunk_id: str) -> int | None:
    """Return a 1-indexed page number, or None when the value is unusable."""
    if page is None:
        return None
    if page < 1:
        logger.warning(
            "chunk %s has non-positive page_number %r; citing without a page",
            chunk_id,
            page,
        )
        return None
    return page


def _clamp(score: float) -> float:
    return max(0.0, min(1.0, score))
