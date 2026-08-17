"""Chunking rules for tender and legal documents."""

from __future__ import annotations

from app.db.models.enums import ChunkType, Language
from app.ingestion.chunking.semantic_chunker import (
    _CLAUSE_START,
    MAX_CHUNK_SIZE,
    chunk_elements,
)
from app.ingestion.parsers.base import ParsedElement

TABLE_HTML = (
    "<table><tr><td>Item</td><td>Qty</td><td>Price</td></tr>"
    "<tr><td>Cable</td><td>250</td><td>1,250.00</td></tr></table>"
)


def _table(page: int = 1, html: str = TABLE_HTML) -> ParsedElement:
    return ParsedElement("Item Qty Price Cable 250 1,250.00", ChunkType.TABLE, page, html=html)


def test_clause_openers_detected_in_both_scripts() -> None:
    for opener in (
        "3.2.14 The Contractor",
        "(a) subject to",
        "Article 7 Payment",
        "المادة ٥ يلتزم",
        "البند 3.2 التوريد",
        "1-2-3. Scope",
    ):
        assert _CLAUSE_START.match(opener), opener
    assert not _CLAUSE_START.match("Ordinary sentence here")


def test_numbered_clauses_split_into_separate_chunks() -> None:
    """A clause cut in half states a requirement without its qualifying
    condition, which is how a bid promises something the tender never asked for.
    """
    elements = [
        ParsedElement("3.2.14 The Contractor shall hold ISO 27001.", ChunkType.NARRATIVE, 1),
        ParsedElement("3.2.15 The Contractor shall provide 24/7 support.", ChunkType.NARRATIVE, 1),
    ]
    chunks = chunk_elements(elements)
    assert len(chunks) == 2
    assert chunks[0].raw_text.startswith("3.2.14")
    assert chunks[1].raw_text.startswith("3.2.15")


def test_table_is_atomic_and_keeps_markup() -> None:
    chunks = chunk_elements([_table()])
    assert len(chunks) == 1
    assert chunks[0].chunk_type is ChunkType.TABLE
    # HTML is cited; the flattened text is only good enough to embed.
    assert chunks[0].raw_text.startswith("<table>")
    assert "<td>Price</td>" in chunks[0].raw_text


def test_oversized_table_repeats_header_in_every_part() -> None:
    """Without the repeated header, later parts are unlabelled columns of
    numbers — still retrievable, still authoritative-looking, meaningless.
    """
    rows = "".join(
        f"<tr><td>Item {i}</td><td>{i * 10}</td><td>{i * 99}.00</td></tr>" for i in range(400)
    )
    header = "<tr><td>Item</td><td>Qty</td><td>Price</td></tr>"
    chunks = chunk_elements([_table(html=f"<table>{header}{rows}</table>")])

    assert len(chunks) > 1
    assert all(header in c.raw_text for c in chunks)
    assert all(len(c.raw_text) <= MAX_CHUNK_SIZE + len(header) for c in chunks)


def test_headers_and_footers_are_dropped() -> None:
    """Page chrome repeats on every page and would bury real content under
    near-identical duplicates.
    """
    chunks = chunk_elements(
        [
            ParsedElement("CONFIDENTIAL - Page 4 of 88", ChunkType.HEADER_FOOTER, 4),
            ParsedElement("The Contractor shall supply all materials.", ChunkType.NARRATIVE, 4),
        ]
    )
    assert len(chunks) == 1
    assert "CONFIDENTIAL" not in chunks[0].raw_text


def test_arabic_prose_splits_on_sentence_terminators() -> None:
    """Arabic has no separator above word level in the default hierarchy, so
    long paragraphs would otherwise be cut mid-word.
    """
    sentence = "يلتزم المقاول بتقديم كافة المستندات المطلوبة وفقاً للشروط المنصوص عليها؛"
    chunks = chunk_elements([ParsedElement(" ".join([sentence] * 40), ChunkType.NARRATIVE, 1)])

    assert len(chunks) > 1
    # Every boundary must land between words, never inside one. The separator
    # itself is consumed by the split, so the test is that each chunk's final
    # token is a whole word from the source.
    words = set(sentence.replace("؛", "").split())
    for chunk in chunks:
        assert chunk.raw_text.split()[-1].rstrip("؛") in words


def test_retrieval_and_display_text_diverge_for_arabic() -> None:
    chunks = chunk_elements(
        [ParsedElement("المادة ٤ يلتزم المقاول بتقديم الضمان البنكي.", ChunkType.NARRATIVE, 1)]
    )
    chunk = chunks[0]
    assert chunk.language is Language.AR
    assert chunk.text != chunk.raw_text  # normalised for matching
    assert "٤" in chunk.raw_text  # original preserved for citation
    assert "4" in chunk.text  # folded for retrieval
