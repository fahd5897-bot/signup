"""Arabic normalisation.

The invariant under test: retrieval text may be folded aggressively, display
text must not change a single visible glyph. Breaking the second one misaligns
every citation highlight from the source PDF.
"""

from __future__ import annotations

import pytest
from app.db.models.enums import Language
from app.ingestion.normalizers.arabic import (
    arabic_ratio,
    normalise_for_display,
    normalise_for_retrieval,
)
from app.ingestion.normalizers.language_detect import detect_language, dominant_language


@pytest.mark.parametrize(
    ("variant_a", "variant_b"),
    [
        ("إجمالي", "اجمالي"),  # hamza-below alef vs bare
        ("أعمال", "اعمال"),  # hamza-above alef vs bare
        ("الشَّرِكَةُ", "الشركة"),  # diacritised vs plain
        ("شــــركة", "شركة"),  # tatweel-stretched vs plain
        ("٢٧٠٠١", "27001"),  # Arabic-Indic vs ASCII digits
    ],
)
def test_orthographic_variants_collapse(variant_a: str, variant_b: str) -> None:
    """Forms a writer treats as identical must match after normalisation."""
    assert normalise_for_retrieval(variant_a) == normalise_for_retrieval(variant_b)


def test_display_normalisation_preserves_glyphs() -> None:
    original = "شهادة الأيزو ٢٧٠٠١"
    assert normalise_for_display(original) == original


def test_display_strips_invisible_bidi_marks() -> None:
    """Bidi controls break offsets and string equality but are never visible."""
    assert normalise_for_display("ISO‏ 27001‎") == "ISO 27001"


def test_numeric_content_is_not_language_tagged() -> None:
    """Regression: Arabic-Indic digits live inside the Arabic codepoint block.

    Counting them as letters tags a bill of quantities priced in Arabic-Indic
    numerals as Arabic prose, hiding it from English-filtered retrieval.
    """
    assert arabic_ratio("٢٧٠٠١ / 3.2.14") == 0.0
    assert detect_language("٢٧٠٠١ / 3.2.14") is Language.UNKNOWN


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("شهادة الأيزو للشركة والمقاول", Language.AR),
        ("ISO 27001 certification held since 2019", Language.EN),
        ("شهادة ISO 27001 معتمدة", Language.MIXED),
        ("", Language.UNKNOWN),
    ],
)
def test_language_detection(text: str, expected: Language) -> None:
    assert detect_language(text) is expected


def test_bilingual_document_rolls_up_to_mixed() -> None:
    """A half-Arabic tender must not be tagged AR, which would hide half of it."""
    assert dominant_language([Language.AR] * 5 + [Language.EN] * 5) is Language.MIXED
    assert dominant_language([Language.AR] * 9 + [Language.EN]) is Language.AR
