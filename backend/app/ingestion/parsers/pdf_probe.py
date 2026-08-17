"""Decide how a PDF should be parsed before paying for the expensive path.

``hi_res`` on a 400-page tender costs minutes of GPU/CPU and real money. Running
it on a born-digital PDF that already has a perfectly good text layer is pure
waste; *not* running it on a scan produces a document that parses "successfully"
into nothing. This module makes that call by looking at the file rather than
guessing from the extension.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.exceptions import CorruptFileError, EncryptedFileError
from app.db.models.enums import ParseStrategy

logger = logging.getLogger(__name__)

#: Mean extractable characters per page below which a PDF is treated as scanned.
#: A born-digital tender page carries well over a thousand; a scanned page
#: yields only stray header text, if anything.
_SCANNED_CHARS_PER_PAGE = 100

#: Pages sampled rather than read in full. Scanned documents are overwhelmingly
#: uniform, and reading 400 pages to answer a yes/no question is its own cost.
_SAMPLE_PAGES = 10


@dataclass(slots=True)
class PdfProbeResult:
    page_count: int
    has_text_layer: bool
    mean_chars_per_page: float
    is_encrypted: bool
    strategy: ParseStrategy


def probe_pdf(data: bytes) -> PdfProbeResult:
    """Inspect a PDF and choose a parse strategy.

    Raises:
        EncryptedFileError: password-protected; no strategy can read it.
        CorruptFileError: the file is not a readable PDF.
    """
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - dependency is declared
        logger.warning("pymupdf unavailable; defaulting to hi_res")
        return PdfProbeResult(0, False, 0.0, False, ParseStrategy.HI_RES)

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # pymupdf raises several unrelated types
        raise CorruptFileError(f"could not open PDF: {exc}") from exc

    try:
        if doc.needs_pass:
            raise EncryptedFileError("PDF is password-protected")

        page_count = doc.page_count
        if page_count == 0:
            raise CorruptFileError("PDF contains no pages")

        sample = min(_SAMPLE_PAGES, page_count)
        # Sample evenly across the document. Contiguous first-N sampling is
        # misleading: tender packs routinely open with a born-digital cover
        # letter and continue with scanned annexes.
        step = max(1, page_count // sample)
        indexes = list(range(0, page_count, step))[:sample]

        total = 0
        for i in indexes:
            try:
                total += len(doc.load_page(i).get_text("text").strip())
            except Exception as exc:
                logger.warning("page %d unreadable during probe: %s", i, exc)

        mean = total / len(indexes) if indexes else 0.0
        has_text = mean >= _SCANNED_CHARS_PER_PAGE

        strategy = ParseStrategy.FAST if has_text else ParseStrategy.OCR_ONLY
        logger.info("pdf probe: pages=%d mean_chars=%.0f -> %s", page_count, mean, strategy.value)
        return PdfProbeResult(page_count, has_text, mean, False, strategy)
    finally:
        doc.close()
