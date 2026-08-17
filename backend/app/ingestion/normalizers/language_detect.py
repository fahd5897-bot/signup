"""Per-block language tagging.

Script-ratio based rather than model based. A trained language identifier is
the right tool for distinguishing Spanish from Portuguese; here the only
question is "Arabic script or Latin script", which the codepoint ranges answer
exactly and for free. Loading fastText for that would add ~1s of import time
and a 130 MB model to every worker.

``MIXED`` is a first-class outcome, not a failure. GCC tender clauses routinely
carry Arabic prose with English defined terms and standard references, and a
chunk tagged MIXED is retrievable from queries in either language.
"""

from __future__ import annotations

from app.db.models.enums import Language
from app.ingestion.normalizers.arabic import arabic_ratio

#: Above this share of Arabic letters, the block is Arabic.
_ARABIC_THRESHOLD = 0.80
#: Below this, it is Latin.
_LATIN_THRESHOLD = 0.20
#: Shorter than this, ratios are noise — a two-word heading swings wildly.
_MIN_CHARS_FOR_CONFIDENCE = 12


def detect_language(text: str) -> Language:
    """Classify a single block of text."""
    stripped = text.strip()
    if not stripped:
        return Language.UNKNOWN

    ratio = arabic_ratio(stripped)

    # No letters at all: a table of pure numbers, a reference code, a bare date.
    # UNKNOWN rather than a guess — these chunks are retrievable either way, and
    # a wrong tag would exclude them from a language-filtered search.
    if ratio == 0.0 and not any(c.isalpha() for c in stripped):
        return Language.UNKNOWN

    if len(stripped) < _MIN_CHARS_FOR_CONFIDENCE:
        return Language.AR if ratio > 0.5 else Language.EN

    if ratio >= _ARABIC_THRESHOLD:
        return Language.AR
    if ratio <= _LATIN_THRESHOLD:
        return Language.EN
    return Language.MIXED


def dominant_language(languages: list[Language]) -> Language:
    """Roll per-block tags up to a document-level tag.

    A document with a substantial minority of blocks in the other language is
    MIXED, not simply the majority language: bilingual tenders are the norm in
    the target market, and tagging one AR would hide half its content from an
    English-filtered search.
    """
    counted = [lang for lang in languages if lang is not Language.UNKNOWN]
    if not counted:
        return Language.UNKNOWN

    total = len(counted)
    arabic = sum(1 for lang in counted if lang is Language.AR)
    english = sum(1 for lang in counted if lang is Language.EN)
    mixed = sum(1 for lang in counted if lang is Language.MIXED)

    if mixed / total > 0.25:
        return Language.MIXED
    if arabic / total >= 0.85:
        return Language.AR
    if english / total >= 0.85:
        return Language.EN
    return Language.MIXED
