"""Requirement extraction: what survives, what is dropped, and why.

The failure this suite exists to prevent is a matrix that looks complete. A
fabricated requirement wastes a reviewer's time; a missed one loses the bid,
and an invented one that is *believed* sends the bidder answering a clause the
tender never contained. So the chain's contract is narrow: a requirement is
real only if its source span is in the document.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.db.models.enums import Language
from app.rag.chains.extract_requirements import (
    RequirementExtractionChain,
    SourceChunk,
    _build_windows,
    _synthetic_ref,
)
from app.rag.prompts.requirement_extraction import TOOL_NAME

ENGLISH = (
    "3.2.14 The contractor shall hold a valid ISO 27001 certificate for the "
    "duration of the contract."
)
ARABIC = "المادة ٥ يجب على المورد تقديم شهادة الزكاة والدخل سارية المفعول."


def _chunks(*texts: str) -> list[SourceChunk]:
    return [
        SourceChunk(raw_text=t, chunk_index=i, page_number=i + 1, section_path="Section 3")
        for i, t in enumerate(texts)
    ]


def _response(*requirements: dict, usage_in: int = 100, usage_out: int = 50):
    """Shape-compatible stand-in for a forced tool-use response."""
    return SimpleNamespace(
        model="claude-haiku-4-5",
        usage=SimpleNamespace(input_tokens=usage_in, output_tokens=usage_out),
        content=[
            SimpleNamespace(
                type="tool_use",
                name=TOOL_NAME,
                input={"requirements": list(requirements)},
            )
        ],
    )


def _chain(monkeypatch, settings, *responses):
    """A chain whose model returns the given responses, one per window."""
    chain = RequirementExtractionChain(anthropic=object(), settings=settings)
    queue = list(responses)

    async def _fake_call(extract, language, index, total):
        return queue[index] if index < len(queue) else _response()

    monkeypatch.setattr(chain, "_call", _fake_call)
    return chain


def _requirement(**overrides) -> dict:
    base = {
        "requirement_ref": "3.2.14",
        "requirement_text": "Do you hold ISO 27001?",
        "source_text": ENGLISH,
        "category": "technical",
        "is_mandatory": True,
    }
    base.update(overrides)
    return base


async def test_a_requirement_with_a_real_source_span_survives(monkeypatch, settings):
    chain = _chain(monkeypatch, settings, _response(_requirement()))
    result = await chain.run(_chunks(ENGLISH))

    assert len(result.requirements) == 1
    found = result.requirements[0]
    assert found.requirement_ref == "3.2.14"
    assert found.source_text == ENGLISH
    assert found.page_number == 1
    assert result.dropped == []


async def test_a_fabricated_requirement_is_dropped(monkeypatch, settings):
    """The model claims a clause the document does not contain."""
    invented = _requirement(
        requirement_ref="9.9",
        requirement_text="Provide a bank guarantee of 10%.",
        source_text="The bidder shall provide a bank guarantee of 10% of the contract value.",
    )
    chain = _chain(monkeypatch, settings, _response(_requirement(), invented))
    result = await chain.run(_chunks(ENGLISH))

    assert [r.requirement_ref for r in result.requirements] == ["3.2.14"]
    assert len(result.dropped) == 1
    assert "does not appear" in result.dropped[0].reason


async def test_a_paraphrased_span_is_dropped_too(monkeypatch, settings):
    """Close enough to look right, which is exactly why it must not pass.

    A paraphrase may describe a real clause, but nothing can prove that from
    the stored row — and an unprovable requirement shown as extracted is
    indistinguishable to the reviewer from a verified one.
    """
    paraphrase = _requirement(
        source_text="The contractor must have ISO 27001 certification throughout the contract."
    )
    chain = _chain(monkeypatch, settings, _response(paraphrase))
    result = await chain.run(_chunks(ENGLISH))

    assert result.requirements == []
    assert len(result.dropped) == 1


async def test_line_wrapping_does_not_break_the_anchor(monkeypatch, settings):
    """PDF extraction rewraps text; a span differing only in whitespace is the
    same span."""
    rewrapped = _requirement(source_text=ENGLISH.replace(" ", "\n  ", 3))
    chain = _chain(monkeypatch, settings, _response(rewrapped))
    result = await chain.run(_chunks(ENGLISH))

    assert len(result.requirements) == 1
    # The stored span keeps the model's own copy, so a citation still aligns
    # with the rendered document rather than with a normalised form.
    assert result.requirements[0].source_text == rewrapped["source_text"]


async def test_arabic_spans_resolve_across_orthographic_variation(monkeypatch, settings):
    """The same Arabic word is written with and without diacritics across a
    tender and its annexes; matching normalises, storage does not."""
    variant = ARABIC.replace("إ", "ا").replace("ى", "ي")
    proposed = _requirement(
        requirement_ref="المادة ٥",
        requirement_text="هل لديكم شهادة زكاة سارية؟",
        source_text=variant,
        category="administrative",
    )
    chain = _chain(monkeypatch, settings, _response(proposed))
    result = await chain.run(_chunks(ARABIC))

    assert len(result.requirements) == 1
    assert result.requirements[0].language is Language.AR


async def test_an_unnumbered_item_gets_a_stable_synthetic_reference(monkeypatch, settings):
    """Re-extraction must not renumber the matrix under the reviewer."""
    unnumbered = _requirement(requirement_ref="")
    chain = _chain(monkeypatch, settings, _response(unnumbered))

    first = await chain.run(_chunks(ENGLISH))
    second = await chain.run(_chunks(ENGLISH))

    ref = first.requirements[0].requirement_ref
    assert ref.startswith("AUTO-")
    assert first.requirements[0].ref_is_synthetic is True
    assert second.requirements[0].requirement_ref == ref


async def test_a_modal_verb_overrides_an_optional_classification(monkeypatch, settings):
    """Fail-closed on obligation.

    An optional-by-mistake requirement drops out of the export gate and the
    submission ships incomplete, so the deterministic pass may only promote.
    """
    mislabelled = _requirement(is_mandatory=False)
    chain = _chain(monkeypatch, settings, _response(mislabelled))
    result = await chain.run(_chunks(ENGLISH))

    assert result.requirements[0].is_mandatory is True


async def test_arabic_obligation_markers_promote_too(monkeypatch, settings):
    mislabelled = _requirement(
        requirement_ref="المادة ٥",
        requirement_text="شهادة الزكاة",
        source_text=ARABIC,
        is_mandatory=False,
    )
    chain = _chain(monkeypatch, settings, _response(mislabelled))
    result = await chain.run(_chunks(ARABIC))

    assert result.requirements[0].is_mandatory is True


async def test_genuinely_optional_text_stays_optional(monkeypatch, settings):
    optional = "3.9 Bidders may include reference projects from the last five years."
    proposed = _requirement(
        requirement_ref="3.9",
        requirement_text="Reference projects (optional).",
        source_text=optional,
        is_mandatory=False,
    )
    chain = _chain(monkeypatch, settings, _response(proposed))
    result = await chain.run(_chunks(optional))

    assert result.requirements[0].is_mandatory is False
    assert result.mandatory_count == 0


async def test_the_same_requirement_seen_in_two_windows_is_recorded_once(monkeypatch, settings):
    """Windows overlap by design, so duplicates are expected, not exceptional."""
    filler = "x " * 7000
    chain = _chain(
        monkeypatch,
        settings,
        _response(_requirement()),
        _response(_requirement()),
    )
    result = await chain.run(_chunks(ENGLISH, filler, ENGLISH))

    assert result.windows >= 2
    assert len(result.requirements) == 1


async def test_an_empty_document_yields_nothing_without_calling_the_model(settings):
    chain = RequirementExtractionChain(anthropic=object(), settings=settings)
    result = await chain.run([])
    assert result.requirements == [] and result.windows == 0


async def test_a_requirement_with_no_text_is_refused(monkeypatch, settings):
    chain = _chain(monkeypatch, settings, _response(_requirement(requirement_text="")))
    result = await chain.run(_chunks(ENGLISH))
    assert result.requirements == []
    assert result.dropped[0].reason == "no requirement text"


async def test_a_requirement_with_no_source_span_is_refused(monkeypatch, settings):
    chain = _chain(monkeypatch, settings, _response(_requirement(source_text="")))
    result = await chain.run(_chunks(ENGLISH))
    assert result.requirements == []
    assert "no source span" in result.dropped[0].reason


async def test_requirements_come_back_in_document_order(monkeypatch, settings):
    """Sorting by reference string would put 3.10 before 3.2, and a reviewer
    reading the tender alongside the matrix would think an item was missing."""
    second = "3.10 The contractor shall submit monthly progress reports."
    chain = _chain(
        monkeypatch,
        settings,
        _response(
            _requirement(requirement_ref="3.10", source_text=second),
            _requirement(),
        ),
    )
    result = await chain.run(_chunks(ENGLISH, second))

    assert [r.requirement_ref for r in result.requirements] == ["3.2.14", "3.10"]


def test_windows_never_cut_a_chunk_in_half():
    """A table split across windows becomes a header row in one and prices in
    the next, and both halves read as separate requirements."""
    chunks = _chunks(*["y" * 5000 for _ in range(5)])
    windows = _build_windows(chunks, 12_000)

    assert len(windows) > 1
    rebuilt = {c.chunk_index for w in windows for c in w}
    assert rebuilt == {c.chunk_index for c in chunks}, "every chunk appears somewhere"
    for window in windows:
        for chunk in window:
            assert chunk.raw_text == chunks[chunk.chunk_index].raw_text


def test_consecutive_windows_overlap():
    """Without the overlap, a requirement straddling the boundary is seen only
    in halves and is never extracted whole."""
    chunks = _chunks(*["z" * 5000 for _ in range(5)])
    windows = _build_windows(chunks, 12_000)

    for earlier, later in zip(windows, windows[1:], strict=False):
        assert earlier[-1].chunk_index == later[0].chunk_index


@pytest.mark.parametrize("spacing", ["  ", "\n", "\t"])
def test_synthetic_references_ignore_whitespace_differences(spacing):
    base = _synthetic_ref("The contractor shall provide a warranty.")
    respaced = _synthetic_ref(f"The{spacing}contractor shall provide a warranty.")
    assert base == respaced
