"""Structure-aware chunking for tender and legal documents.

Generic recursive character splitting is wrong for this corpus, in two ways that
both cause wrong answers rather than merely poor ones:

1. **It splits tables.** Half a bill of quantities is worse than none — the
   surviving half still retrieves, still looks authoritative, and no longer has
   the header row that says what the numbers mean.

2. **It splits clauses.** Tender obligations are numbered and self-contained
   ("3.2.14 The Contractor shall..."). A clause cut in half produces a chunk
   that states a requirement without its qualifying condition, which is how a
   bid ends up promising something the tender never asked for.

So: tables are atomic, clause boundaries are hard split points, and headings are
carried into every chunk they govern. Recursive character splitting is retained
only as the last resort for a single oversized paragraph.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.db.models.enums import ChunkType, Language
from app.ingestion.normalizers.arabic import normalise_for_retrieval
from app.ingestion.normalizers.language_detect import detect_language
from app.ingestion.parsers.base import ParsedElement

logger = logging.getLogger(__name__)

#: Target chunk size in characters.
#:
#: Characters, not tokens, and deliberately so. Arabic encodes to roughly twice
#: the tokens per character of English on most tokenizers, so a token-based
#: target silently yields Arabic chunks half the physical size of their English
#: equivalents — the bilingual corpus ends up with two different chunk
#: granularities and retrieval quality diverges by language. A character target
#: keeps them comparable.
DEFAULT_CHUNK_SIZE = 1_800
DEFAULT_OVERLAP = 200
#: Chunks below this are merged into a neighbour; a stranded heading or a single
#: line embeds to near-noise.
MIN_CHUNK_SIZE = 120
#: Hard ceiling. Tables above this are split by rows with the header repeated.
MAX_CHUNK_SIZE = 8_000

#: Numbered clause openers, both scripts. Matches "3.2.14", "3-2-14", "(a)",
#: "Article 5", "المادة ٥", "البند 3.2". These are hard boundaries: a chunk
#: should never begin mid-clause.
_CLAUSE_START = re.compile(
    r"^\s*(?:"
    r"\(?\d+(?:[.\-]\d+){0,4}\)?[.\)]?\s+"  # 3.2.14  / (3.2) / 3-2-14.
    r"|\(?[a-zA-Z]\)[\s]"  # (a)
    r"|(?:Article|Clause|Section|Item)\s+\d+"  # Article 5
    r"|(?:المادة|البند|الفقرة|القسم)\s*[\d٠-٩]+"  # Arabic equivalents
    r")",
    re.MULTILINE,
)

#: Paragraph, then line, then sentence (both scripts — note the Arabic full stop
#: and question mark), then word. Applied only inside an oversized paragraph.
_SPLIT_HIERARCHY = ["\n\n", "\n", "۔ ", ". ", "؟ ", "? ", "! ", "؛ ", "; ", "، ", ", ", " "]


@dataclass(slots=True)
class Chunk:
    """A retrievable unit, ready to embed."""

    #: Retrieval-normalised text — what gets embedded.
    text: str
    #: Original text, exactly as it appears in the source. Citations quote this.
    raw_text: str
    chunk_type: ChunkType
    chunk_index: int
    page_number: int | None = None
    section_path: str | None = None
    language: Language = Language.UNKNOWN
    bbox: list[float] | None = None
    char_start: int | None = None
    char_end: int | None = None
    #: True when this chunk opens a numbered clause ("3.2.14", "المادة ٥").
    #: Blocks the undersized-merge step from gluing it onto the previous
    #: clause — tender clauses are frequently under the merge threshold, and
    #: merging them silently undoes the clause-boundary splitting above.
    starts_clause: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.raw_text)


def chunk_elements(
    elements: list[ParsedElement],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Turn parsed elements into retrievable chunks.

    Tables become one chunk each (or a header-repeating series if oversized).
    Narrative text is accumulated under its governing heading and flushed at
    clause boundaries or when the target size is reached.
    """
    chunks: list[Chunk] = []
    buffer: list[ParsedElement] = []

    def flush() -> None:
        if buffer:
            chunks.extend(_emit_narrative(buffer, chunk_size, overlap, len(chunks)))
            buffer.clear()

    for element in elements:
        # Headers, footers, and page numbers are chrome. They repeat on every
        # page, so indexing them buries real content under near-identical
        # near-duplicates.
        if element.chunk_type is ChunkType.HEADER_FOOTER:
            continue

        if element.is_table:
            flush()  # a table ends whatever narrative preceded it
            chunks.extend(_emit_table(element, len(chunks)))
            continue

        # A heading opens a new section; never carry narrative across it.
        if element.chunk_type is ChunkType.TITLE:
            flush()
            buffer.append(element)
            continue

        # A new numbered clause is a hard boundary.
        if buffer and _CLAUSE_START.match(element.text):
            flush()

        buffer.append(element)

        if sum(e.char_count for e in buffer) >= chunk_size:
            flush()

    flush()
    merged = _merge_undersized(chunks)
    for i, chunk in enumerate(merged):
        chunk.chunk_index = i
    logger.info("chunked %d elements into %d chunks", len(elements), len(merged))
    return merged


# ----------------------------------------------------------------- internals
def _emit_table(element: ParsedElement, start_index: int) -> list[Chunk]:
    """One table -> one chunk, unless it exceeds the hard ceiling.

    The HTML markup is what gets stored and cited; the flattened text is used
    only for the embedding, since an embedding of raw ``<td>`` tags is mostly
    markup noise.
    """
    body = element.html or element.text
    if len(body) <= MAX_CHUNK_SIZE:
        return [
            Chunk(
                text=normalise_for_retrieval(element.text),
                raw_text=body,
                chunk_type=ChunkType.TABLE,
                chunk_index=start_index,
                page_number=element.page_number,
                section_path=element.section_path,
                language=element.language,
                bbox=element.bbox,
                metadata={"is_html": element.html is not None, "sheet": element.page_name},
            )
        ]

    # Oversized: split by rows, repeating the header in every part. Without the
    # repeated header the later parts are columns of unlabelled numbers.
    return _split_table_by_rows(element, start_index)


def _split_table_by_rows(element: ParsedElement, start_index: int) -> list[Chunk]:
    html = element.html or ""
    rows = re.findall(r"<tr[^>]*>.*?</tr>", html, flags=re.DOTALL | re.IGNORECASE)
    if not rows:
        # No parseable rows — fall back to character splitting so the content is
        # at least retrievable, and flag it for the quality report.
        return [
            Chunk(
                text=normalise_for_retrieval(part),
                raw_text=part,
                chunk_type=ChunkType.TABLE,
                chunk_index=start_index + i,
                page_number=element.page_number,
                section_path=element.section_path,
                language=element.language,
                metadata={"split": "character", "degraded": True},
            )
            for i, part in enumerate(_recursive_split(element.text, MAX_CHUNK_SIZE, 0))
        ]

    header, data_rows = rows[0], rows[1:]
    chunks: list[Chunk] = []
    current: list[str] = []
    size = len(header)

    def emit() -> None:
        if not current:
            return
        markup = f"<table>{header}{''.join(current)}</table>"
        text = re.sub(r"<[^>]+>", " ", markup)
        chunks.append(
            Chunk(
                text=normalise_for_retrieval(text),
                raw_text=markup,
                chunk_type=ChunkType.TABLE,
                chunk_index=start_index + len(chunks),
                page_number=element.page_number,
                section_path=element.section_path,
                language=element.language,
                metadata={
                    "is_html": True,
                    "split": "rows",
                    "part": len(chunks) + 1,
                    "header_repeated": True,
                },
            )
        )

    for row in data_rows:
        if size + len(row) > MAX_CHUNK_SIZE and current:
            emit()
            current, size = [], len(header)
        current.append(row)
        size += len(row)
    emit()

    logger.info("split oversized table into %d parts, header repeated", len(chunks))
    return chunks


def _emit_narrative(
    elements: list[ParsedElement], chunk_size: int, overlap: int, start_index: int
) -> list[Chunk]:
    """Join buffered elements and split if still oversized."""
    text = "\n\n".join(e.text for e in elements if e.text).strip()
    if not text:
        return []

    first = elements[0]
    # Prefer a page number from an element with real content: a section's first
    # element is often a heading stranded at the foot of the previous page, and
    # citing that page sends the reviewer to the wrong place.
    page = next(
        (e.page_number for e in elements if e.page_number is not None and e.char_count > 20),
        first.page_number,
    )

    parts = _recursive_split(text, chunk_size, overlap) if len(text) > chunk_size else [text]
    opens_clause = bool(_CLAUSE_START.match(text))

    return [
        Chunk(
            text=normalise_for_retrieval(part),
            raw_text=part,
            chunk_type=first.chunk_type
            if first.chunk_type is not ChunkType.TITLE
            else ChunkType.NARRATIVE,
            chunk_index=start_index + i,
            page_number=page,
            section_path=first.section_path,
            language=detect_language(part),
            bbox=first.bbox if len(parts) == 1 else None,
            starts_clause=opens_clause and i == 0,
            metadata={"part": i + 1, "of": len(parts)} if len(parts) > 1 else {},
        )
        for i, part in enumerate(parts)
    ]


def _recursive_split(text: str, size: int, overlap: int) -> list[str]:
    """Split on the most semantic separator that works, most-preferred first.

    Equivalent in spirit to LangChain's RecursiveCharacterTextSplitter, but with
    Arabic sentence terminators in the hierarchy (``۔`` ``؟`` ``؛`` ``،``).
    Without them, Arabic prose has no usable separator above the word level and
    every long Arabic paragraph degrades to mid-word splitting.
    """
    if len(text) <= size:
        return [text]

    for separator in _SPLIT_HIERARCHY:
        if separator not in text:
            continue
        parts: list[str] = []
        current = ""
        for piece in text.split(separator):
            candidate = f"{current}{separator}{piece}" if current else piece
            if len(candidate) > size and current:
                parts.append(current.strip())
                # Carry a tail of the previous chunk so a statement split across
                # the boundary is still retrievable from either side.
                current = (current[-overlap:] + separator + piece) if overlap else piece
            else:
                current = candidate
        if current.strip():
            parts.append(current.strip())
        if len(parts) > 1:
            return [p for p in parts if p]

    # No separator helped — a single unbroken run. Hard-cut it.
    return [text[i : i + size] for i in range(0, len(text), size - overlap or size)]


def _merge_undersized(chunks: list[Chunk]) -> list[Chunk]:
    """Fold tiny narrative chunks into the following one.

    Two things are never merged. Tables, because their value is being a
    complete self-contained grid. And chunks that open a numbered clause,
    because tender clauses are routinely shorter than the merge threshold and
    merging them would quietly undo the clause-boundary splitting that is the
    whole point of this module.
    """
    if not chunks:
        return []

    merged: list[Chunk] = []
    for chunk in chunks:
        if (
            merged
            and chunk.chunk_type is not ChunkType.TABLE
            and merged[-1].chunk_type is not ChunkType.TABLE
            and not chunk.starts_clause
            and merged[-1].char_count < MIN_CHUNK_SIZE
            and merged[-1].page_number == chunk.page_number
        ):
            previous = merged.pop()
            chunk = Chunk(
                text=f"{previous.text}\n\n{chunk.text}".strip(),
                raw_text=f"{previous.raw_text}\n\n{chunk.raw_text}".strip(),
                chunk_type=chunk.chunk_type,
                chunk_index=previous.chunk_index,
                page_number=previous.page_number,
                section_path=previous.section_path or chunk.section_path,
                language=chunk.language,
                bbox=previous.bbox,
                starts_clause=previous.starts_clause,
                metadata=chunk.metadata,
            )
        merged.append(chunk)
    return merged
