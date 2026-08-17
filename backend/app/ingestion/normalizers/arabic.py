"""Arabic text normalisation for retrieval.

The governing rule of this module: **normalise for matching, never for
display**. Every function here returns text used to build embeddings and lexical
indexes. The original bytes are preserved separately in ``ChunkPayload.raw_text``
and are what citations quote, because a highlight computed against
dediacritised text will not align with the glyphs in the rendered PDF.

Implemented with the standard library rather than CAMeL Tools for the hot path:
these are character-level substitutions over every chunk of every document, and
a regex table is roughly two orders of magnitude faster than loading CAMeL's
morphological database. CAMeL is still the right tool for lemmatisation in
query expansion, where the cost is paid once per query.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------- tables

#: Harakat (short vowels), tanween, shadda, sukun, and the Quranic annotation
#: range. Diacritics are optional in written Arabic, so the *same* word appears
#: with and without them across a tender and its annexes. Stripping them is what
#: makes those forms match.
_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")

#: Tatweel/kashida — a pure typographic stretch character with no semantic
#: content. Common in justified tender text and in scanned OCR output.
_TATWEEL = re.compile(r"ـ")

#: Alef forms: bare, madda, hamza above/below, and the rarely-typed wasla.
#: Writers are wildly inconsistent here, and "إجمالي" vs "اجمالي" is a
#: recall-killing mismatch on exactly the pricing terms that matter.
_ALEF = re.compile(r"[آأإٱٲٳ]")

#: Alef maqsura -> ya. The distinction is orthographic, not semantic, and is
#: routinely dropped in Gulf typing conventions.
_ALEF_MAKSURA = re.compile(r"ى")

#: Ta marbuta -> ha. Same reasoning: inconsistently typed word-finally.
_TA_MARBUTA = re.compile(r"ة")

#: Arabic-Indic (٠-٩) and extended Arabic-Indic (۰-۹) digits.
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

#: Presentation forms (U+FB50-U+FEFF). OCR and older PDF generators emit these
#: ligature/positional glyphs instead of base letters; left alone they match
#: nothing. NFKC folds them back to canonical form.
_PRESENTATION_FORMS = re.compile(r"[ﭐ-﷿ﹰ-﻿]")

#: Bidi control marks. Invisible, and they corrupt token boundaries.
_BIDI_CONTROLS = re.compile(r"[​-‏‪-‮⁦-⁩﻿]")

_WHITESPACE = re.compile(r"[ \t  - ]+")
_NEWLINES = re.compile(r"\n{3,}")

#: Arabic *letters* only. The digit sub-ranges (U+0660-0669 Arabic-Indic and
#: U+06F0-06F9 extended) are deliberately excluded: they live inside the Arabic
#: block, so counting them as letters tags a pure-numeric table — a bill of
#: quantities priced in Arabic-Indic numerals — as Arabic prose, which then
#: hides it from any English-filtered search.
_ARABIC_CHAR = re.compile(
    "[\u0600-\u065f\u066e-\u06ef\u06fa-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]"
)
_LATIN_CHAR = re.compile(r"[A-Za-z]")


def has_arabic(text: str) -> bool:
    return bool(_ARABIC_CHAR.search(text))


def arabic_ratio(text: str) -> float:
    """Share of letter characters that are Arabic. 0.0 when there are none."""
    arabic = len(_ARABIC_CHAR.findall(text))
    latin = len(_LATIN_CHAR.findall(text))
    total = arabic + latin
    return arabic / total if total else 0.0


def strip_bidi_controls(text: str) -> str:
    """Remove invisible directional marks.

    Applied to display text too — these are never wanted. PDF extractors insert
    them liberally around mixed Arabic/Latin runs, and they break both string
    equality and character offsets used for citation highlighting.
    """
    return _BIDI_CONTROLS.sub("", text)


def normalise_for_display(text: str) -> str:
    """Minimal cleanup safe for text a human will read.

    Only removes what is invisible or purely typographic. This is what goes into
    ``raw_text`` and therefore into citation quotes, so it must not change any
    glyph the reader would see in the source document.
    """
    text = strip_bidi_controls(text)
    # NFC (compose) rather than NFKC: it repairs decomposed sequences without
    # rewriting presentation forms, which would alter what the reader sees.
    text = unicodedata.normalize("NFC", text)
    text = _WHITESPACE.sub(" ", text)
    text = _NEWLINES.sub("\n\n", text)
    return text.strip()


def normalise_for_retrieval(text: str) -> str:
    """Aggressive normalisation for embedding and lexical matching.

    Order matters. NFKC must run before the letter-folding rules so that
    presentation-form glyphs have already become base letters by the time the
    alef and ya rules see them.
    """
    text = strip_bidi_controls(text)

    # Fold presentation forms and width variants to canonical letters.
    text = unicodedata.normalize("NFKC", text)

    text = _DIACRITICS.sub("", text)
    text = _TATWEEL.sub("", text)
    text = _ALEF.sub("ا", text)  # all alef variants -> bare alef
    text = _ALEF_MAKSURA.sub("ي", text)  # alef maqsura -> ya
    text = _TA_MARBUTA.sub("ه", text)  # ta marbuta -> ha

    # Arabic-Indic digits -> ASCII, so "٢٧٠٠١" and "27001" match. Essential:
    # standards and tender reference numbers are written both ways, sometimes
    # within one document.
    text = text.translate(_ARABIC_INDIC)

    text = _WHITESPACE.sub(" ", text)
    text = _NEWLINES.sub("\n\n", text)
    return text.strip().lower()
