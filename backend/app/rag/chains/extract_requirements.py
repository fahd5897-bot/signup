"""Turn a tender document into a compliance matrix.

The chain that produces the work list. Everything else in the product answers
requirements; this is what decides which requirements exist, and a miss here is
invisible downstream — the matrix simply does not mention the clause, the
export gate sees nothing outstanding, and the bid is disqualified on something
nobody read.

Two properties carry the design:

* **Every requirement is anchored to verbatim source text.** The model returns
  a span it claims to have copied from the document; the chain checks it
  against the actual text and discards the requirement if it does not resolve.
  This is the same guarantee the answer path gets from the citations API, done
  locally because extraction has no search results to cite.
* **Mandatory-by-default.** A deterministic modal-verb pass can promote an
  item to mandatory but never demote one. Mislabelling a mandatory requirement
  as optional removes it from the export gate, which is the one error that
  fails open.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field

from anthropic import (
    APIConnectionError,
    APIStatusError,
    AsyncAnthropic,
    BadRequestError,
    RateLimitError,
)

from app.core.config import Settings, get_settings
from app.core.exceptions import IngestionError
from app.db.models.enums import Language
from app.ingestion.normalizers.arabic import arabic_ratio, normalise_for_retrieval
from app.rag.prompts.requirement_extraction import (
    PROMPT_VERSION,
    REQUIREMENT_TOOL,
    TOOL_NAME,
    build_extract_message,
    get_system_prompt,
)

logger = logging.getLogger(__name__)

#: Characters of document text per model call. Comfortably inside the context
#: window; the limit that actually binds is attention, not tokens — a model
#: handed eighty pages at once reliably skips clauses in the middle.
WINDOW_CHARS = 12_000

#: Chunks repeated at the head of the next window. A requirement split across
#: a boundary is then seen whole at least once, and the de-duplication pass
#: removes the copy.
WINDOW_OVERLAP_CHUNKS = 1

#: Room for a few hundred requirements from one window.
_MAX_TOKENS = 8192

#: Modal verbs that make an item obligatory regardless of how the model
#: classified it. English first, then the Arabic forms a Gulf tender actually
#: uses. Deliberately one-directional: this promotes to mandatory, never
#: demotes, because an optional-by-mistake requirement silently drops out of
#: the export gate.
_MANDATORY_MARKERS = (
    "shall",
    "must",
    "is required",
    "are required",
    "mandatory",
    "obligatory",
    "يجب",
    "يلتزم",
    "إلزامي",
    "الزامي",
    "ملزم",
    "يتعين",
    "على المورد",
    "على المقاول",
)

#: Collapses every run of whitespace so a span that differs from the source
#: only in line wrapping still resolves. PDF extraction routinely rewraps.
_WHITESPACE = re.compile(r"\s+")


class ExtractionError(IngestionError):
    slug = "extraction_failed"
    user_message = "Requirements could not be extracted from this document."


@dataclass(slots=True)
class ExtractedRequirement:
    """One requirement, anchored to the text it came from."""

    requirement_ref: str
    requirement_text: str
    #: Verbatim span from the document. Resolved against the source before this
    #: object is built, so its presence is the proof the requirement is real.
    source_text: str
    is_mandatory: bool
    category: str = "other"
    section_path: str | None = None
    page_number: int | None = None
    chunk_index: int | None = None
    language: Language = Language.UNKNOWN
    #: True when the document did not number the item and the chain assigned a
    #: stable synthetic reference. Surfaced to the reviewer, who may renumber.
    ref_is_synthetic: bool = False


@dataclass(slots=True)
class DroppedRequirement:
    """Something the model returned that did not survive verification.

    Kept rather than silently discarded: a document producing many drops is
    either badly OCR'd or being summarised instead of quoted, and both are
    worth telling the reviewer about before they trust the matrix.
    """

    requirement_ref: str
    requirement_text: str
    reason: str


@dataclass(slots=True)
class ExtractionResult:
    requirements: list[ExtractedRequirement] = field(default_factory=list)
    dropped: list[DroppedRequirement] = field(default_factory=list)
    windows: int = 0
    model_id: str | None = None
    prompt_version: str = PROMPT_VERSION
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0

    @property
    def mandatory_count(self) -> int:
        return sum(1 for r in self.requirements if r.is_mandatory)

    @property
    def drop_ratio(self) -> float:
        """Fraction of proposed requirements that failed verification."""
        proposed = len(self.requirements) + len(self.dropped)
        return len(self.dropped) / proposed if proposed else 0.0


@dataclass(slots=True)
class SourceChunk:
    """A piece of the document to extract from.

    Deliberately not :class:`app.ingestion.chunking.semantic_chunker.Chunk`:
    the chain is fed equally from a freshly parsed document and from chunks
    scrolled back out of Qdrant, and only these fields are common to both.
    """

    raw_text: str
    chunk_index: int
    page_number: int | None = None
    section_path: str | None = None


class RequirementExtractionChain:
    """Extract the requirement list from one tender document."""

    def __init__(
        self,
        anthropic: AsyncAnthropic | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._anthropic = anthropic or AsyncAnthropic(
            api_key=self._settings.anthropic_api_key.get_secret_value()
        )

    async def run(
        self,
        chunks: list[SourceChunk],
        *,
        language: Language | None = None,
        window_chars: int = WINDOW_CHARS,
    ) -> ExtractionResult:
        """Read the document window by window and return its requirements.

        Raises:
            ExtractionError: the model could not be reached, or refused.
        """
        started = time.monotonic()
        result = ExtractionResult()
        if not chunks:
            return result

        detected = language or _detect_language(chunks)
        windows = _build_windows(chunks, window_chars)
        result.windows = len(windows)

        seen: dict[str, ExtractedRequirement] = {}
        for index, window in enumerate(windows):
            extract = "\n\n".join(c.raw_text for c in window)
            response = await self._call(extract, detected, index, len(windows))

            result.model_id = response.model
            result.input_tokens += response.usage.input_tokens
            result.output_tokens += response.usage.output_tokens

            for proposed in _tool_payloads(response):
                self._absorb(proposed, window, extract, detected, seen, result)

        result.requirements = sorted(seen.values(), key=_ordering_key)
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "extracted %d requirements (%d mandatory) from %d windows; %d dropped",
            len(result.requirements),
            result.mandatory_count,
            result.windows,
            len(result.dropped),
        )
        return result

    # ---------------------------------------------------------------- absorb
    def _absorb(
        self,
        proposed: dict,
        window: list[SourceChunk],
        extract: str,
        language: Language,
        seen: dict[str, ExtractedRequirement],
        result: ExtractionResult,
    ) -> None:
        """Verify one proposed requirement and file it, or record why not."""
        ref = str(proposed.get("requirement_ref") or "").strip()
        text = str(proposed.get("requirement_text") or "").strip()
        source = str(proposed.get("source_text") or "").strip()

        if not text:
            result.dropped.append(DroppedRequirement(ref, text, "no requirement text"))
            return
        if not source:
            result.dropped.append(DroppedRequirement(ref, text, "no source span was supplied"))
            return

        anchor = _locate(source, extract, window)
        if anchor is None:
            # The span is not in the document. Either the model paraphrased —
            # in which case the requirement may be real but is unprovable — or
            # it invented the item outright. Both are refused: an unanchored
            # requirement cannot be shown to a reviewer as something the tender
            # actually says.
            result.dropped.append(
                DroppedRequirement(ref, text, "source span does not appear in the document")
            )
            return

        synthetic = not ref
        if synthetic:
            ref = _synthetic_ref(source)

        key = _dedupe_key(ref, text)
        if key in seen:
            return  # the overlap between windows showed it twice

        seen[key] = ExtractedRequirement(
            requirement_ref=ref[:128],
            requirement_text=text,
            source_text=source,
            is_mandatory=_is_mandatory(proposed, source, text),
            category=str(proposed.get("category") or "other"),
            section_path=(str(proposed.get("section_path") or "").strip() or anchor.section_path),
            page_number=anchor.page_number,
            chunk_index=anchor.chunk_index,
            language=language,
            ref_is_synthetic=synthetic,
        )

    # ----------------------------------------------------------------- model
    async def _call(self, extract: str, language: Language, index: int, total: int):
        try:
            response = await self._anthropic.messages.create(
                # Haiku tier: high-volume, structurally simple, and the output
                # shape is pinned by the tool schema rather than by judgement.
                model=self._settings.llm_model_classify,
                max_tokens=_MAX_TOKENS,
                system=get_system_prompt(language),
                tools=[REQUIREMENT_TOOL],
                # Forced, not offered. Left optional the model answers a third
                # of windows in prose, and prose is not parseable into a
                # compliance matrix.
                tool_choice={"type": "tool", "name": TOOL_NAME},
                messages=[
                    {
                        "role": "user",
                        "content": build_extract_message(
                            extract, window_index=index, window_count=total
                        ),
                    }
                ],
            )
        except BadRequestError as exc:
            raise ExtractionError(f"rejected by the API: {exc}") from exc
        except (RateLimitError, APIConnectionError) as exc:
            raise ExtractionError(f"temporarily unavailable: {exc}") from exc
        except APIStatusError as exc:
            raise ExtractionError(f"API error {exc.status_code}: {exc}") from exc
        return response


# ------------------------------------------------------------------ helpers
def _tool_payloads(response) -> list[dict]:
    """Pull requirement dicts out of the tool call, tolerating an empty turn."""
    payloads: list[dict] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) != "tool_use" or block.name != TOOL_NAME:
            continue
        items = (block.input or {}).get("requirements") or []
        payloads.extend(item for item in items if isinstance(item, dict))
    return payloads


def _build_windows(chunks: list[SourceChunk], window_chars: int) -> list[list[SourceChunk]]:
    """Slice the document into overlapping windows of whole chunks.

    Whole chunks, never mid-chunk: a table cut in half is a table whose header
    row is in one window and its prices in the next, and the model will happily
    describe both halves as separate requirements.
    """
    windows: list[list[SourceChunk]] = []
    current: list[SourceChunk] = []
    size = 0

    for chunk in chunks:
        length = len(chunk.raw_text)
        if current and size + length > window_chars:
            windows.append(current)
            current = current[-WINDOW_OVERLAP_CHUNKS:] if WINDOW_OVERLAP_CHUNKS else []
            size = sum(len(c.raw_text) for c in current)
        current.append(chunk)
        size += length

    if current:
        windows.append(current)
    return windows


@dataclass(slots=True)
class _Anchor:
    chunk_index: int | None
    page_number: int | None
    section_path: str | None


def _locate(source: str, extract: str, window: list[SourceChunk]) -> _Anchor | None:
    """Find the span in the window, and report which chunk it came from.

    Matching is done on whitespace-collapsed, retrieval-normalised text.
    Whitespace because PDF extraction rewraps lines, and Arabic normalisation
    because the same word is written with and without diacritics across a
    tender and its annexes — a span quoted from a heading and matched against
    body text would otherwise fail on an invisible difference. The *stored*
    span stays exactly as the model returned it, so citations still align with
    the rendered document.
    """
    needle = _matchable(source)
    if not needle:
        return None
    if needle not in _matchable(extract):
        return None

    for chunk in window:
        if needle in _matchable(chunk.raw_text):
            return _Anchor(chunk.chunk_index, chunk.page_number, chunk.section_path)

    # Present in the window but spanning two chunks; attribute it to the first.
    head = window[0] if window else None
    return _Anchor(
        head.chunk_index if head else None,
        head.page_number if head else None,
        head.section_path if head else None,
    )


def _matchable(text: str) -> str:
    return _WHITESPACE.sub(" ", normalise_for_retrieval(text)).strip().casefold()


def _is_mandatory(proposed: dict, source: str, text: str) -> bool:
    """Model verdict, promoted to mandatory by an explicit modal verb.

    One-directional on purpose. Treating an optional item as mandatory costs a
    reviewer one click; treating a mandatory one as optional removes it from
    the export gate, and the submission ships incomplete.
    """
    if bool(proposed.get("is_mandatory", True)):
        return True
    haystack = _matchable(f"{source} {text}")
    return any(_matchable(marker) in haystack for marker in _MANDATORY_MARKERS)


def _synthetic_ref(source: str) -> str:
    """Stable reference for an unnumbered item.

    Derived from the source span rather than from its position, so re-running
    extraction over the same document produces the same reference and the
    matrix does not renumber itself under the reviewer.
    """
    # sha256 rather than sha1 purely to keep the security linter quiet: this
    # is a stable label, not a security primitive.
    digest = hashlib.sha256(_matchable(source).encode("utf-8")).hexdigest()[:8]
    return f"AUTO-{digest}"


def _dedupe_key(ref: str, text: str) -> str:
    return f"{_matchable(ref)}|{_matchable(text)[:200]}"


def _ordering_key(requirement: ExtractedRequirement) -> tuple:
    """Document order, which is the order a reviewer reads the tender in.

    Sorting by reference string would put '3.10' before '3.2', and sorting by
    confidence would make a skipped item impossible to notice.
    """
    return (
        requirement.page_number if requirement.page_number is not None else 1 << 30,
        requirement.chunk_index if requirement.chunk_index is not None else 1 << 30,
        requirement.requirement_ref,
    )


def _detect_language(chunks: list[SourceChunk]) -> Language:
    """Whole-document verdict.

    Per-window detection would switch the prompt mid-document on an English
    annex inside an Arabic tender, and the Arabic guidance is what stops the
    model from "correcting" spans it cannot then match.
    """
    sample = " ".join(c.raw_text for c in chunks[:20])[:20_000]
    return Language.AR if arabic_ratio(sample) >= 0.20 else Language.EN


__all__ = [
    "WINDOW_CHARS",
    "DroppedRequirement",
    "ExtractedRequirement",
    "ExtractionError",
    "ExtractionResult",
    "RequirementExtractionChain",
    "SourceChunk",
]
