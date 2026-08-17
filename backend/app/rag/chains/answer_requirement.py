"""The grounded-answer chain: question in, cited and verified answer out.

Five stages, three of which can stop the pipeline before it produces text:

    retrieve -> rerank -> [GATE: evidence] -> generate -> map citations
             -> verify -> [GATE: coverage] -> persistable result

The gates are the product. Anything that reaches a reviewer has survived a
retrieval-score threshold, server-computed citations resolved against the
supplied chunks, and a coverage check — and still requires a human signature
before it can be exported.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

from anthropic import (
    APIConnectionError,
    APIStatusError,
    AsyncAnthropic,
    BadRequestError,
    RateLimitError,
)
from qdrant_client import AsyncQdrantClient

from app.core.config import Settings, get_settings
from app.core.exceptions import IngestionError
from app.db.models.enums import GroundingVerdict, Language, ProposalStatus
from app.rag.grounding.citation_mapper import map_citations
from app.rag.grounding.context_builder import build_search_results
from app.rag.grounding.verifier import compute_confidence, verify
from app.rag.prompts.answer_generation import (
    NOT_FOUND_SENTINEL,
    PROMPT_VERSION,
    build_question,
    get_system_prompt,
)
from app.rag.retrieval.hybrid import RetrievedChunk, retrieve
from app.rag.retrieval.reranker import Reranker
from app.schemas.proposal import Citation

logger = logging.getLogger(__name__)

#: Tender answers are paragraphs, not essays. A low ceiling is a hard
#: requirement here rather than a cost saving: an answer that rambles past the
#: requirement invites unsupported sentences, and every one of those drags
#: citation coverage down.
_MAX_TOKENS = 4096

#: Candidates retrieved before reranking.
_RETRIEVE_K = 30


class GenerationError(IngestionError):
    slug = "generation_failed"
    user_message = "The answer could not be generated."


@dataclass(slots=True)
class AnswerResult:
    """Everything one generation produced, ready to persist as a proposal."""

    requirement_ref: str
    requirement_text: str
    answer_text: str | None
    language: Language
    citations: list[Citation] = field(default_factory=list)
    retrieved_chunk_ids: list[str] = field(default_factory=list)
    status: ProposalStatus = ProposalStatus.DRAFT
    grounding_verdict: GroundingVerdict = GroundingVerdict.NOT_APPLICABLE
    citation_coverage: float | None = None
    top_retrieval_score: float | None = None
    confidence_score: float | None = None
    unsupported_sentences: list[str] = field(default_factory=list)
    abstention_reason: str | None = None
    model_id: str | None = None
    prompt_version: str = PROMPT_VERSION
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    generation_ms: int | None = None

    @property
    def abstained(self) -> bool:
        return self.status is ProposalStatus.ABSTAINED


class AnswerChain:
    """Answers one tender requirement from a tenant's own corpus."""

    def __init__(
        self,
        qdrant: AsyncQdrantClient,
        anthropic: AsyncAnthropic | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._qdrant = qdrant
        self._anthropic = anthropic or AsyncAnthropic(
            api_key=self._settings.anthropic_api_key.get_secret_value()
        )
        self._reranker = Reranker(self._settings)

    async def run(
        self,
        *,
        requirement_ref: str,
        requirement_text: str,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        language: Language = Language.EN,
        top_k: int = 8,
        style_hint: str | None = None,
        min_retrieval_score: float | None = None,
    ) -> AnswerResult:
        """Produce one grounded answer, or abstain."""
        started = time.monotonic()
        threshold = (
            min_retrieval_score
            if min_retrieval_score is not None
            else self._settings.min_retrieval_score
        )

        # --- retrieve -------------------------------------------------------
        candidates = await retrieve(
            query=requirement_text,
            tenant_id=tenant_id,
            qdrant=self._qdrant,
            workspace_id=workspace_id,
            top_k=_RETRIEVE_K,
            settings=self._settings,
        )
        if not candidates:
            return self._abstain(
                requirement_ref,
                requirement_text,
                language,
                "no matching content in the knowledge base",
                started,
            )

        chunks = await self._reranker.rerank(requirement_text, candidates, top_k=top_k)
        top_score = chunks[0].score if chunks else 0.0

        # --- GATE 1: is there evidence worth spending a generation on? ------
        if top_score < threshold:
            # No model call at all. Generating from weak evidence produces a
            # fluent answer that cites a barely-relevant passage — the hardest
            # kind for a reviewer to catch, because it looks properly sourced.
            return self._abstain(
                requirement_ref,
                requirement_text,
                language,
                (
                    f"best match scored {top_score:.2f}, below the "
                    f"{threshold:.2f} evidence threshold"
                ),
                started,
                chunks=chunks,
                top_score=top_score,
            )

        # --- generate -------------------------------------------------------
        response = await self._generate(requirement_text, chunks, language, style_hint)

        # --- map citations back onto the supplied chunks --------------------
        mapped = map_citations(response.content, chunks)

        # --- verify ---------------------------------------------------------
        report = verify(mapped, settings=self._settings)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        if report.is_abstention:
            result = self._abstain(
                requirement_ref,
                requirement_text,
                language,
                report.protocol_violation or "the sources did not support an answer",
                started,
                chunks=chunks,
                top_score=top_score,
            )
            result.model_id = response.model
            result.input_tokens = response.usage.input_tokens
            result.output_tokens = response.usage.output_tokens
            return result

        return AnswerResult(
            requirement_ref=requirement_ref,
            requirement_text=requirement_text,
            answer_text=mapped.text,
            language=language,
            citations=mapped.citations,
            retrieved_chunk_ids=[c.chunk_id for c in chunks],
            # DRAFT, never APPROVED. Nothing in this pipeline may approve an
            # answer; that requires a named human (enforced by a CHECK
            # constraint on the proposals table).
            status=ProposalStatus.DRAFT,
            grounding_verdict=report.verdict,
            citation_coverage=report.coverage,
            top_retrieval_score=round(top_score, 4),
            confidence_score=compute_confidence(report, top_retrieval_score=top_score),
            unsupported_sentences=report.unsupported_sentences,
            model_id=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", None),
            generation_ms=elapsed_ms,
        )

    # ------------------------------------------------------------------ model
    async def _generate(
        self,
        requirement_text: str,
        chunks: list[RetrievedChunk],
        language: Language,
        style_hint: str | None,
    ):
        """Call Claude with the retrieved chunks as citable search results."""
        search_results = build_search_results(chunks)

        # Cache the evidence block. Regenerating an answer, or answering several
        # requirements that retrieve overlapping chunks, then re-reads the
        # context at cache rates. The breakpoint goes on the last search result
        # so the volatile question stays outside the cached prefix.
        if search_results:
            search_results[-1]["cache_control"] = {"type": "ephemeral"}

        content = [
            *search_results,
            {"type": "text", "text": build_question(requirement_text, style_hint)},
        ]

        try:
            response = await self._anthropic.beta.messages.create(
                model=self._settings.llm_model_generation,
                max_tokens=_MAX_TOKENS,
                system=get_system_prompt(language),
                messages=[{"role": "user", "content": content}],
                # Adaptive thinking helps the model notice that evidence is
                # adjacent-but-insufficient — the judgement the abstention rule
                # depends on. No temperature: it is rejected on Opus 5.
                thinking={"type": "adaptive"},
                output_config={"effort": self._settings.llm_effort},
                # Route around a safety refusal rather than surfacing a hard
                # failure to a bid manager on deadline.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except BadRequestError as exc:
            # Not retryable: a malformed request fails identically next time.
            raise GenerationError(f"rejected by the API: {exc}") from exc
        except (RateLimitError, APIConnectionError) as exc:
            raise GenerationError(f"temporarily unavailable: {exc}") from exc
        except APIStatusError as exc:
            raise GenerationError(f"API error {exc.status_code}: {exc}") from exc

        if response.stop_reason == "refusal":
            # A safety classifier declined. Surfaced as an abstention by the
            # verifier, since there is no answer text to ground.
            details = getattr(response, "stop_details", None)
            logger.warning("generation refused (category=%s)", getattr(details, "category", None))

        return response

    # -------------------------------------------------------------- abstention
    def _abstain(
        self,
        requirement_ref: str,
        requirement_text: str,
        language: Language,
        reason: str,
        started: float,
        *,
        chunks: list[RetrievedChunk] | None = None,
        top_score: float | None = None,
    ) -> AnswerResult:
        """Build an abstention.

        ``answer_text`` stays None deliberately — the database CHECK constraint
        requires it. A placeholder such as "no information available" is what
        reviewers skim past and approve; an empty answer with a stated reason
        forces the gap to be dealt with.
        """
        logger.info("abstained on %s: %s", requirement_ref, reason)
        return AnswerResult(
            requirement_ref=requirement_ref,
            requirement_text=requirement_text,
            answer_text=None,
            language=language,
            retrieved_chunk_ids=[c.chunk_id for c in (chunks or [])],
            status=ProposalStatus.ABSTAINED,
            grounding_verdict=GroundingVerdict.NOT_APPLICABLE,
            top_retrieval_score=round(top_score, 4) if top_score is not None else None,
            confidence_score=0.0,
            abstention_reason=reason,
            generation_ms=int((time.monotonic() - started) * 1000),
        )


__all__ = ["AnswerChain", "AnswerResult", "GenerationError", "NOT_FOUND_SENTINEL"]
