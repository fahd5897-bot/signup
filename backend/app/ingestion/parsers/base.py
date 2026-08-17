"""Parser-independent representation of a parsed document.

Every parser returns :class:`ParseResult` regardless of the underlying library,
so chunking, normalisation, and embedding never learn what produced an element.
That boundary is what makes it possible to swap the PDF path (Unstructured →
a commercial OCR service for hard Arabic scans) without touching anything
downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.db.models.enums import ChunkType, Language, ParseStrategy


@dataclass(slots=True)
class ParsedElement:
    """One structural unit as the parser found it, before chunking."""

    text: str
    chunk_type: ChunkType
    #: 1-indexed. For spreadsheets this is the sheet ordinal.
    page_number: int | None = None
    #: Sheet name, or a PDF's page label where one exists.
    page_name: str | None = None
    #: Table markup. Populated for TABLE elements and nothing else.
    #:
    #: Retained because the flattened ``text`` of a table is unusable as
    #: evidence: "Item Qty Price 1 Cable 250 1,250.00" gives a model no way to
    #: know which price belongs to which row, and a confidently mis-attributed
    #: unit price in a bid is exactly the failure this product exists to prevent.
    html: str | None = None
    #: Heading breadcrumb assembled from the document's title hierarchy.
    section_path: str | None = None
    #: Nesting depth of the owning heading; drives section-path assembly.
    category_depth: int | None = None
    #: [x0, y0, x1, y1] in PDF user space, for citation highlighting.
    bbox: list[float] | None = None
    language: Language = Language.UNKNOWN
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_table(self) -> bool:
        return self.chunk_type is ChunkType.TABLE

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass(slots=True)
class ParseResult:
    """Everything one parse produced, plus how well it went."""

    elements: list[ParsedElement]
    strategy: ParseStrategy
    page_count: int | None = None
    #: Share of pages that yielded usable text. The primary quality signal for
    #: Arabic scans — see :class:`app.core.exceptions.EmptyExtractionError`.
    text_extraction_ratio: float | None = None
    language: Language = Language.UNKNOWN
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_chars(self) -> int:
        return sum(e.char_count for e in self.elements)

    @property
    def table_count(self) -> int:
        return sum(1 for e in self.elements if e.is_table)
