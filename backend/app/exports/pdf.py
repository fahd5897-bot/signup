"""PDF rendering, via HTML so the layout rules are readable.

WeasyPrint is used rather than a PDF drawing library because right-to-left
layout, mixed-direction runs, and page-breaking are all things CSS already
knows how to do correctly, and re-implementing any of them on top of a canvas
API is how Arabic submissions end up with punctuation on the wrong side.

The one thing CSS cannot rescue is a missing font. A system with no
Arabic-capable face renders every Arabic glyph as a box, and the PDF looks
plausible enough at a glance to be submitted. So an Arabic export checks for a
usable font first and refuses rather than producing that file.
"""

from __future__ import annotations

import functools
import logging
from typing import Final

from app.core.exceptions import AppError
from app.exports.models import ExportBundle, ExportRow

logger = logging.getLogger(__name__)

MEDIA_TYPE: Final = "application/pdf"

#: Codepoints that must be drawable before an Arabic PDF is worth producing:
#: two common letters and the Arabic comma. A font claiming Arabic coverage
#: without these is not one.
_ARABIC_PROBE: Final = (0x0645, 0x0639, 0x060C)


class ArabicFontMissingError(AppError):
    """No installed font can draw Arabic.

    Deliberately fatal. A PDF of boxes is not a degraded export, it is an
    unusable file that looks like a successful one, and it would be discovered
    by the procurement authority rather than by the bidder.
    """

    slug = "arabic_font_missing"
    status_code = 500
    user_message = "The server cannot render Arabic PDFs because no Arabic font is installed."


def build_pdf(bundle: ExportBundle) -> bytes:
    """Render the response document as PDF bytes.

    Raises:
        ArabicFontMissingError: Arabic content with no font that can draw it.
    """
    if bundle.is_rtl and not has_arabic_font():
        raise ArabicFontMissingError(
            "no installed font covers the Arabic range; install fonts-noto-core "
            "or an equivalent in the runtime image"
        )

    from weasyprint import HTML  # imported lazily: pulls in cairo/pango

    html = _render_html(bundle)
    return HTML(string=html).write_pdf()


@functools.lru_cache(maxsize=1)
def has_arabic_font() -> bool:
    """Whether any installed font can draw Arabic.

    Cached: it walks every font file on the system, and the answer cannot
    change without a restart.
    """
    try:
        from fontTools.ttLib import TTFont
    except ImportError:  # pragma: no cover - fontTools ships with weasyprint
        logger.warning("fontTools unavailable; assuming an Arabic font is present")
        return True

    import glob

    for path in glob.glob("/usr/share/fonts/**/*.tt[fc]", recursive=True) + glob.glob(
        "/usr/local/share/fonts/**/*.tt[fc]", recursive=True
    ):
        try:
            font = TTFont(path, fontNumber=0, lazy=True)
            covered: set[int] = set()
            for table in font["cmap"].tables:
                covered |= set(table.cmap.keys())
            if all(codepoint in covered for codepoint in _ARABIC_PROBE):
                logger.info("arabic-capable font found: %s", path)
                return True
        except Exception as exc:  # noqa: BLE001 - one bad font file is not fatal
            logger.debug("skipping unreadable font %s: %s", path, exc)
            continue
    return False


# ------------------------------------------------------------------ internals
_CSS = """
@page {
    size: A4;
    margin: 22mm 18mm;
    @bottom-center { content: counter(page) " / " counter(pages); font-size: 9pt; }
}
body { font-family: "Noto Sans Arabic", "DejaVu Sans", sans-serif; font-size: 11pt;
       line-height: 1.7; color: #111827; }
h1 { font-size: 20pt; margin-bottom: 4mm; }
/* A requirement and its answer must not be split across a page: a heading
   stranded at the bottom of a page reads as an unanswered requirement. */
section { break-inside: avoid; margin-bottom: 7mm; }
h2 { font-size: 12pt; color: #1f2937; margin-bottom: 2mm; break-after: avoid; }
.meta { color: #6b7280; font-size: 9pt; }
/* Direction-agnostic isolation. A Latin standard name inside an Arabic
   sentence — and an Arabic clause inside an English one — both need it. */
p, h2 { unicode-bidi: isolate; }
"""


def _render_html(bundle: ExportBundle) -> str:
    direction = "rtl" if bundle.is_rtl else "ltr"
    lang = "ar" if bundle.is_rtl else "en"
    title = "الرد على كراسة الشروط" if bundle.is_rtl else "Tender Response"
    prepared = "أُعد بواسطة" if bundle.is_rtl else "Prepared by"

    body = "\n".join(_section(row) for row in bundle.answered_rows)
    return (
        f'<!DOCTYPE html><html lang="{lang}" dir="{direction}"><head>'
        f'<meta charset="utf-8"><style>{_CSS}</style></head><body>'
        f"<h1>{_escape(title)}</h1>"
        f'<p class="meta">{_escape(prepared)}: {_escape(bundle.tenant_name)} — '
        f"{_escape(bundle.workspace_name)}</p>"
        f'<p class="meta">{bundle.generated_at.strftime("%Y-%m-%d %H:%M UTC")}</p>'
        f"{body}</body></html>"
    )


def _section(row: ExportRow) -> str:
    heading = f"{row.requirement_ref} — {row.requirement_text}".strip(" —")
    paragraphs = "".join(
        # dir="auto" per paragraph: an answer may be in the other script from
        # the document as a whole, and the browser/renderer decides from the
        # first strong character rather than from our guess.
        f'<p dir="auto">{_escape(block.strip())}</p>'
        for block in (row.answer_text or "").split("\n")
        if block.strip()
    )
    return f'<section><h2 dir="auto">{_escape(heading)}</h2>{paragraphs}</section>'


def _escape(text: str) -> str:
    import html

    return html.escape(text, quote=False)


__all__ = ["MEDIA_TYPE", "ArabicFontMissingError", "build_pdf", "has_arabic_font"]
