"""The response document — what actually goes to the procurement authority.

python-docx has no API for right-to-left text, so the two attributes that make
an Arabic document readable (``w:bidi`` on the paragraph, ``w:rtl`` on the run)
are set directly on the underlying XML. Without them Word lays Arabic out
left-to-right: the glyphs are correct, the line order is not, and punctuation
lands on the wrong end of every sentence. A submitted tender that looks like
that is a lost bid.
"""

from __future__ import annotations

import io
from typing import Final

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.paragraph import Paragraph

from app.exports.models import ExportBundle, ExportRow

MEDIA_TYPE: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

#: A font with real Arabic coverage. Word substitutes silently when a font
#: lacks glyphs, and the substitute is rarely one that shapes Arabic well.
_ARABIC_FONT: Final = "Arial"
_LATIN_FONT: Final = "Calibri"

_TITLE_AR: Final = "الرد على كراسة الشروط والمواصفات"
_TITLE_EN: Final = "Tender Response"
_PREPARED_AR: Final = "أُعد بواسطة"
_PREPARED_EN: Final = "Prepared by"


def build_response_docx(bundle: ExportBundle) -> bytes:
    """Render the approved answers, in the tender's own order."""
    document = Document()
    _set_default_font(document, bundle.is_rtl)

    title = _TITLE_AR if bundle.is_rtl else _TITLE_EN
    _paragraph(document, title, bundle.is_rtl, style="Title")
    prepared = _PREPARED_AR if bundle.is_rtl else _PREPARED_EN
    _paragraph(
        document,
        f"{prepared}: {bundle.tenant_name} — {bundle.workspace_name}",
        bundle.is_rtl,
    )
    _paragraph(document, bundle.generated_at.strftime("%Y-%m-%d %H:%M UTC"), bundle.is_rtl)
    document.add_page_break()

    for row in bundle.answered_rows:
        _write_requirement(document, row, bundle.is_rtl)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ------------------------------------------------------------------ internals
def _write_requirement(document, row: ExportRow, rtl: bool) -> None:
    heading = f"{row.requirement_ref} — {row.requirement_text}".strip(" —")
    _paragraph(document, heading, rtl, style="Heading 2")
    for block in (row.answer_text or "").split("\n"):
        if block.strip():
            _paragraph(document, block.strip(), rtl)


def _paragraph(document, text: str, rtl: bool, *, style: str | None = None) -> Paragraph:
    paragraph = document.add_paragraph(style=style) if style else document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT if rtl else WD_ALIGN_PARAGRAPH.LEFT
    if rtl:
        _mark_paragraph_rtl(paragraph)
    run = paragraph.add_run(text)
    if rtl:
        _mark_run_rtl(run)
    return paragraph


def _mark_paragraph_rtl(paragraph: Paragraph) -> None:
    """``<w:bidi/>`` — paragraph-level right-to-left layout.

    This is what reverses the order of runs and puts the sentence-final full
    stop on the left. Alignment alone does not do it: a right-aligned
    left-to-right paragraph still reads in the wrong direction.
    """
    properties = paragraph._p.get_or_add_pPr()
    if properties.find(qn("w:bidi")) is None:
        properties.append(properties.makeelement(qn("w:bidi"), {}))


def _mark_run_rtl(run) -> None:
    """``<w:rtl/>`` — marks the run's characters as right-to-left.

    Needed in addition to ``w:bidi``: without it Word applies the *Latin* font
    to Arabic characters and picks its own substitute when that font has no
    Arabic glyphs.
    """
    properties = run._r.get_or_add_rPr()
    if properties.find(qn("w:rtl")) is None:
        properties.append(properties.makeelement(qn("w:rtl"), {}))


def _set_default_font(document, rtl: bool) -> None:
    """Set both the Latin and the complex-script font on the Normal style.

    Word tracks them separately (``w:cs`` is the complex-script face). Setting
    only the Latin one leaves Arabic to whatever the reader's machine chooses,
    which is how a submission renders differently on the evaluator's screen
    than on the bidder's.
    """
    style = document.styles["Normal"]
    style.font.name = _ARABIC_FONT if rtl else _LATIN_FONT
    style.font.size = Pt(11)

    element = style.element.rPr.rFonts
    element.set(qn("w:ascii"), _LATIN_FONT)
    element.set(qn("w:hAnsi"), _LATIN_FONT)
    element.set(qn("w:cs"), _ARABIC_FONT)


__all__ = ["MEDIA_TYPE", "build_response_docx"]
