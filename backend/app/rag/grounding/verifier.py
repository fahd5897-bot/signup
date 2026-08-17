"""Post-generation grounding check.

Runs after the model answers and before a human ever sees the text. It asks one
question: is every factual claim in this answer tied to a citation that resolves
to a chunk we supplied?

Deterministic, not model-based. A second LLM asked "is this grounded?" is
another thing that can be confidently wrong, and it would fail in the same
direction as the first. Sentence segmentation and citation coverage are
mechanical, cheap, and their failure modes are inspectable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.db.models.enums import GroundingVerdict
from app.rag.grounding.citation_mapper import MappedAnswer
from app.rag.prompts.answer_generation import NOT_FOUND_SENTINEL

logger = logging.getLogger(__name__)

#: Sentence terminators in both scripts. Arabic full stop (۔), question mark
#: (؟), and semicolon (؛) are absent from every English-centric splitter, so
#: omitting them would treat an entire Arabic answer as one sentence — which
#: would score 100% coverage from a single citation.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?۔؟؛])\s+|\n+")

#: A sentence with no letters (a bare figure, a heading number) carries no
#: claim of its own and is excluded from the denominator.
_HAS_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)

#: Below this many characters a fragment is a heading or list marker, not an
#: assertion.
_MIN_CLAIM_CHARS = 25


@dataclass(slots=True)
class GroundingReport:
    verdict: GroundingVerdict
    coverage: float
    total_claims: int
    supported_claims: int
    unsupported_sentences: list[str]
    citation_count: int
    #: Set when the model returned the not-found sentinel.
    is_abstention: bool = False
    #: Set when the model produced something other than a clean answer or a
    #: clean sentinel — e.g. the sentinel with extra prose around it.
    protocol_violation: str | None = None

    @property
    def requires_human_edit(self) -> bool:
        return self.verdict in (GroundingVerdict.PARTIAL, GroundingVerdict.UNVERIFIED)


def verify(
    answer: MappedAnswer,
    *,
    settings: Settings | None = None,
) -> GroundingReport:
    """Score how well an answer is supported by its citations."""
    settings = settings or get_settings()
    text = answer.text.strip()

    # --- integrity: a citation we could not resolve invalidates the answer ---
    if answer.rejected_count:
        return GroundingReport(
            verdict=GroundingVerdict.UNVERIFIED,
            coverage=0.0,
            total_claims=0,
            supported_claims=0,
            unsupported_sentences=[],
            citation_count=len(answer.citations),
            protocol_violation=(
                f"{answer.rejected_count} citation(s) did not resolve to any supplied source"
            ),
        )

    # --- abstention ---------------------------------------------------------
    if text == NOT_FOUND_SENTINEL:
        return GroundingReport(
            verdict=GroundingVerdict.NOT_APPLICABLE,
            coverage=1.0,
            total_claims=0,
            supported_claims=0,
            unsupported_sentences=[],
            citation_count=0,
            is_abstention=True,
        )

    # The sentinel embedded in a longer answer means the model half-complied:
    # it declared a gap and then answered anyway. Treated as an abstention,
    # because the declared gap is the load-bearing part.
    if NOT_FOUND_SENTINEL in text:
        logger.warning("model emitted the not-found sentinel inside a longer answer")
        return GroundingReport(
            verdict=GroundingVerdict.UNVERIFIED,
            coverage=0.0,
            total_claims=0,
            supported_claims=0,
            unsupported_sentences=[],
            citation_count=len(answer.citations),
            is_abstention=True,
            protocol_violation="not-found sentinel returned alongside other text",
        )

    # --- coverage -----------------------------------------------------------
    if not text:
        return GroundingReport(
            verdict=GroundingVerdict.UNVERIFIED,
            coverage=0.0,
            total_claims=0,
            supported_claims=0,
            unsupported_sentences=[],
            citation_count=0,
            protocol_violation="empty answer",
        )

    claims = _claim_sentences(text)
    unsupported = [s for s in claims if _is_unsupported(s, answer)]
    total = len(claims)
    supported = total - len(unsupported)
    coverage = supported / total if total else 0.0

    if not answer.citations:
        verdict = GroundingVerdict.UNVERIFIED
    elif coverage >= settings.min_citation_coverage:
        verdict = GroundingVerdict.VERIFIED
    elif coverage > 0:
        verdict = GroundingVerdict.PARTIAL
    else:
        verdict = GroundingVerdict.UNVERIFIED

    logger.info(
        "grounding: %s coverage=%.2f (%d/%d claims, %d citations)",
        verdict.value,
        coverage,
        supported,
        total,
        len(answer.citations),
    )
    return GroundingReport(
        verdict=verdict,
        coverage=round(coverage, 4),
        total_claims=total,
        supported_claims=supported,
        unsupported_sentences=unsupported,
        citation_count=len(answer.citations),
    )


def _claim_sentences(text: str) -> list[str]:
    """Split into sentences that assert something."""
    return [
        s.strip()
        for s in _SENTENCE_SPLIT.split(text)
        if s.strip() and len(s.strip()) >= _MIN_CLAIM_CHARS and _HAS_LETTERS.search(s)
    ]


def _is_unsupported(sentence: str, answer: MappedAnswer) -> bool:
    """True when a sentence appears only in spans that carried no citation.

    Substring containment rather than exact match, because the API splits text
    blocks at citation boundaries — one sentence routinely spans a cited block
    and an uncited connective, and demanding an exact match would flag it.
    """
    return any(sentence in segment for segment in answer.uncited_segments)


def compute_confidence(
    report: GroundingReport,
    *,
    top_retrieval_score: float,
) -> float:
    """Advisory triage score for the reviewer.

    Deliberately never a gate. Reviewers over-trust a high number, so this
    exists to order a queue, not to decide anything: approval always requires a
    human, whatever this says.

    Weighted toward retrieval quality, because a fluent answer built on weak
    evidence is the dangerous case and citation coverage alone will not reveal
    it — the model can cite a barely-relevant passage perfectly.
    """
    if report.is_abstention:
        return 0.0
    if report.verdict is GroundingVerdict.UNVERIFIED:
        return round(0.25 * report.coverage, 4)

    retrieval = max(0.0, min(1.0, top_retrieval_score))
    score = 0.55 * retrieval + 0.45 * report.coverage
    if report.verdict is GroundingVerdict.PARTIAL:
        score *= 0.7
    return round(max(0.0, min(1.0, score)), 4)
