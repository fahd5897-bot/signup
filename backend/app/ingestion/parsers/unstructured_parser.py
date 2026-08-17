"""Unstructured.io parsing, with Arabic and table handling as first concerns.

Two things this module is careful about:

**Strategy selection.** ``hi_res`` is expensive and ``fast`` is useless on a
scan, so PDFs are probed first (see :mod:`app.ingestion.parsers.pdf_probe`) and
the strategy is chosen from what the file actually contains.

**Table fidelity.** Tables are carried as HTML, never as flattened text. The
flattened form of a bill of quantities reads ``"Item Qty Price 1 Cable 250
1,250.00"``, from which no model can recover which price belongs to which row.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from app.core.exceptions import (
    CorruptFileError,
    EmptyExtractionError,
    EncryptedFileError,
    IngestionError,
    UnsupportedFormatError,
)
from app.db.models.enums import ChunkType, ParseStrategy
from app.ingestion.normalizers.arabic import normalise_for_display
from app.ingestion.normalizers.language_detect import detect_language, dominant_language
from app.ingestion.parsers.base import ParsedElement, ParseResult
from app.ingestion.parsers.pdf_probe import probe_pdf

logger = logging.getLogger(__name__)

#: Tesseract language packs. Order matters — Unstructured passes it through to
#: tesseract, which weights earlier languages more heavily. Arabic leads because
#: a mis-OCR'd Arabic clause is unrecoverable, whereas English words inside an
#: Arabic document survive an Arabic-first pass largely intact.
OCR_LANGUAGES: Final[list[str]] = ["ara", "eng"]

#: Unstructured category -> our ChunkType. Anything unmapped becomes NARRATIVE,
#: which is the safe default: it stays retrievable.
_CATEGORY_MAP: Final[dict[str, ChunkType]] = {
    "Title": ChunkType.TITLE,
    "Header": ChunkType.HEADER_FOOTER,
    "Footer": ChunkType.HEADER_FOOTER,
    "NarrativeText": ChunkType.NARRATIVE,
    "Text": ChunkType.NARRATIVE,
    "UncategorizedText": ChunkType.NARRATIVE,
    "ListItem": ChunkType.LIST,
    "Table": ChunkType.TABLE,
    "TableChunk": ChunkType.TABLE,
    "FigureCaption": ChunkType.IMAGE_CAPTION,
    "Image": ChunkType.IMAGE_CAPTION,
    "Formula": ChunkType.NARRATIVE,
    "CheckBox": ChunkType.FORM_FIELD,
    "FormKeyValuePair": ChunkType.FORM_FIELD,
    "Address": ChunkType.NARRATIVE,
    "EmailAddress": ChunkType.NARRATIVE,
    "PageNumber": ChunkType.HEADER_FOOTER,
    "CodeSnippet": ChunkType.NARRATIVE,
}

#: Below this many extracted characters, treat the parse as having failed even
#: though the library reported success. See EmptyExtractionError.
_MIN_USABLE_CHARS = 50

_SPREADSHEET_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
        "text/csv",
    }
)


def parse_document(
    data: bytes,
    *,
    filename: str,
    mime_type: str,
) -> ParseResult:
    """Parse raw bytes into structural elements.

    Args:
        data: The complete file.
        filename: Original name; Unstructured uses the extension as a hint.
        mime_type: Verified content type.

    Raises:
        UnsupportedFormatError: no partitioner for this type.
        EncryptedFileError: password-protected.
        CorruptFileError: unreadable.
        EmptyExtractionError: parsed, but yielded no usable text.
        IngestionError: any other parse failure (retryable).
    """
    if not data:
        raise CorruptFileError("file is empty")

    strategy, probe = _choose_strategy(data, mime_type)
    elements = _partition(data, filename=filename, mime_type=mime_type, strategy=strategy)
    parsed = _convert(elements)

    page_count = probe.page_count if probe else _infer_page_count(parsed)
    ratio = _extraction_ratio(parsed, page_count)

    total_chars = sum(e.char_count for e in parsed)
    if total_chars < _MIN_USABLE_CHARS:
        # Deliberately an error, not an empty success. A READY document with no
        # text is an evidence source that silently contributes nothing, and the
        # weak answers that follow look like a model problem rather than an
        # ingestion one.
        raise EmptyExtractionError(
            f"extracted only {total_chars} characters from {filename!r} "
            f"using strategy {strategy.value!r}",
            strategy=strategy.value,
            page_count=page_count,
        )

    warnings: list[str] = []
    if ratio is not None and ratio < 0.60:
        warnings.append(f"only {ratio:.0%} of pages yielded text; scan quality may be poor")

    result = ParseResult(
        elements=parsed,
        strategy=strategy,
        page_count=page_count,
        text_extraction_ratio=ratio,
        language=dominant_language([e.language for e in parsed]),
        warnings=warnings,
        metadata={"table_count": sum(1 for e in parsed if e.is_table)},
    )
    logger.info(
        "parsed %s: %d elements, %d tables, %d chars, strategy=%s, lang=%s",
        filename,
        len(parsed),
        result.table_count,
        total_chars,
        strategy.value,
        result.language.value,
    )
    return result


# ----------------------------------------------------------------- internals
def _choose_strategy(data: bytes, mime_type: str) -> tuple[ParseStrategy, Any]:
    """Pick a parse strategy, probing PDFs to avoid wasting hi_res on text."""
    if mime_type in _SPREADSHEET_TYPES:
        # Spreadsheets have real cell structure; there is nothing to infer and
        # no OCR to run.
        return ParseStrategy.NATIVE_TABULAR, None

    if mime_type == "application/pdf":
        probe = probe_pdf(data)  # raises on encrypted/corrupt
        if probe.strategy is ParseStrategy.OCR_ONLY:
            # No text layer: OCR is the only option, and table structure has to
            # be inferred visually, so this is the hi_res path.
            return ParseStrategy.HI_RES, probe
        return ParseStrategy.FAST, probe

    return ParseStrategy.FAST, None


def _partition(data: bytes, *, filename: str, mime_type: str, strategy: ParseStrategy) -> list[Any]:
    """Call Unstructured, translating its failures into domain errors."""
    import io

    try:
        from unstructured.partition.auto import partition
    except ImportError as exc:  # pragma: no cover
        raise IngestionError(f"unstructured is not installed: {exc}") from exc

    kwargs: dict[str, Any] = {
        "file": io.BytesIO(data),
        "metadata_filename": filename,
        "content_type": mime_type,
    }

    if strategy is ParseStrategy.NATIVE_TABULAR:
        # Spreadsheets carry real cell structure; each sheet comes back as one
        # Table element with `text_as_html` already populated.
        kwargs["strategy"] = "fast"
    else:
        kwargs["strategy"] = "hi_res" if strategy is ParseStrategy.HI_RES else "fast"
        kwargs["languages"] = OCR_LANGUAGES

        # Table inference must be requested explicitly. `partition()` defaults
        # `skip_infer_table_types` to ["pdf", "jpg", "png", "heic"] — that is,
        # table structure is OFF for PDFs out of the box, and a tender parsed
        # with the defaults silently loses every pricing grid it contains.
        #
        # `infer_table_structure` is the supported override and takes precedence
        # over the skip list. The older `pdf_infer_table_structure` kwarg does
        # the same job but is deprecated and emits a warning.
        kwargs["infer_table_structure"] = True
        kwargs["skip_infer_table_types"] = []

    try:
        return list(partition(**kwargs))
    except ImportError as exc:
        # Unstructured raises ImportError for a *format* whose optional extra is
        # not installed — a deployment problem that presents as a per-file
        # failure. Surfaced as unsupported rather than retried forever.
        raise UnsupportedFormatError(
            f"no partitioner available for {mime_type!r}: {exc}", mime_type=mime_type
        ) from exc
    except (EncryptedFileError, CorruptFileError):
        raise
    except Exception as exc:
        message = str(exc).lower()
        if "password" in message or "encrypt" in message:
            raise EncryptedFileError(str(exc)) from exc
        # "Partitioning is not supported for the FileType.UNK file type" — the
        # bytes are not a format Unstructured knows. Retrying cannot help, and
        # an unbounded retry loop on OCR is expensive.
        if "not supported" in message or "unsupported" in message:
            raise UnsupportedFormatError(
                f"cannot parse {filename!r}: {exc}", mime_type=mime_type
            ) from exc
        if any(k in message for k in ("corrupt", "damaged", "not a", "invalid", "cannot read")):
            raise CorruptFileError(f"could not parse {filename!r}: {exc}") from exc
        # Unknown cause — retryable, because transient OCR/memory failures look
        # like this and a genuine corruption will fail identically next time and
        # exhaust its retries.
        raise IngestionError(f"parse failed for {filename!r}: {exc}") from exc


def _convert(elements: list[Any]) -> list[ParsedElement]:
    """Map Unstructured elements onto ``ParsedElement``, tracking headings."""
    converted: list[ParsedElement] = []
    #: Heading stack, indexed by depth, used to build the section breadcrumb.
    heading_stack: list[str] = []

    for element in elements:
        category = type(element).__name__
        chunk_type = _CATEGORY_MAP.get(category, ChunkType.NARRATIVE)
        metadata = element.metadata

        raw_text = (element.text or "").strip()
        html = getattr(metadata, "text_as_html", None)

        # A table with markup but no flattened text is still valuable; anything
        # else with no text is noise.
        if not raw_text and not html:
            continue

        depth = getattr(metadata, "category_depth", None)

        if chunk_type is ChunkType.TITLE and raw_text:
            level = depth if isinstance(depth, int) else 0
            del heading_stack[level:]
            heading_stack.append(raw_text)

        # Display normalisation only: strips invisible bidi marks and repairs
        # decomposed sequences without altering a single visible glyph, so
        # citation quotes still align with the rendered source.
        text = normalise_for_display(raw_text)

        converted.append(
            ParsedElement(
                text=text,
                chunk_type=chunk_type,
                page_number=getattr(metadata, "page_number", None),
                page_name=getattr(metadata, "page_name", None),
                html=html if chunk_type is ChunkType.TABLE else None,
                section_path=" > ".join(heading_stack) if heading_stack else None,
                category_depth=depth if isinstance(depth, int) else None,
                bbox=_extract_bbox(metadata),
                language=detect_language(text or (html or "")),
                metadata={"category": category},
            )
        )

    return converted


def _extract_bbox(metadata: Any) -> list[float] | None:
    """Reduce Unstructured's coordinate polygon to [x0, y0, x1, y1].

    The polygon is per-corner and may be rotated; the reviewer UI draws an
    axis-aligned highlight rectangle, so the bounding box is what is needed.
    """
    coords = getattr(metadata, "coordinates", None)
    points = getattr(coords, "points", None)
    if not points:
        return None
    try:
        xs = [float(p[0]) for p in points]
        ys = [float(p[1]) for p in points]
        return [min(xs), min(ys), max(xs), max(ys)]
    except (TypeError, ValueError, IndexError):
        return None


def _infer_page_count(elements: list[ParsedElement]) -> int | None:
    pages = [e.page_number for e in elements if e.page_number is not None]
    return max(pages) if pages else None


def _extraction_ratio(elements: list[ParsedElement], page_count: int | None) -> float | None:
    """Share of pages that produced usable text.

    The single most useful quality signal for Arabic scans: a document can parse
    without error yet yield text on only a third of its pages, and that document
    must not be trusted as evidence.
    """
    if not page_count:
        return None
    pages_with_text = {
        e.page_number for e in elements if e.page_number is not None and e.char_count > 20
    }
    return min(1.0, len(pages_with_text) / page_count)
