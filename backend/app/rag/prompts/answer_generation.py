"""System prompts for grounded answer generation.

The prompt is one of four defences, not the defence. Retrieval scoring gates
what reaches the model, the citations API computes source offsets server-side,
and a verifier checks coverage afterwards. This file's job is to make the
model's *default* behaviour correct so the other three rarely have to fire.

Two deliberate choices:

* **The refusal string is exact and machine-checked.** ``NOT_FOUND_SENTINEL``
  is compared literally by the verifier, so "I could not find..." phrasings are
  caught as non-compliant rather than passed through as answers.
* **Prompts are versioned.** ``PROMPT_VERSION`` is written onto every generated
  proposal, so when quality shifts it is possible to tell which prompt produced
  which answers, and to re-run the evaluation set against the previous one.
"""

from __future__ import annotations

from typing import Final

from app.db.models.enums import Language

PROMPT_VERSION: Final = "answer-gen/2026-08-17"

#: Returned verbatim when the sources do not support an answer.
#:
#: Callers compare this exactly. It is intentionally a fixed English string
#: even for Arabic responses: it is a control signal that the pipeline branches
#: on, and a translated variant would have to be matched by locale, which is
#: exactly the kind of near-miss that lets an ungrounded answer through. The
#: Arabic text a reviewer *sees* is supplied by the frontend from
#: ``NOT_FOUND_DISPLAY``, keyed off this sentinel.
NOT_FOUND_SENTINEL: Final = "Information not found in the knowledge base."

#: Localised display strings for the sentinel. UI-facing only.
NOT_FOUND_DISPLAY: Final[dict[Language, str]] = {
    Language.EN: NOT_FOUND_SENTINEL,
    Language.AR: "لا تتوفر هذه المعلومة في قاعدة المعرفة.",
}


_SHARED_RULES = f"""\
You are answering on behalf of a company bidding for a contract. Your answers \
are submitted to a procurement authority as part of a formal, legally binding \
tender response.

## Absolute rules

1. Answer ONLY from the search results provided in this conversation. They are \
your entire universe of fact. Your own knowledge of standards, regulations, \
products, or this company is NOT a permitted source, however confident you are.

2. If the search results do not contain the information needed to answer, reply \
with exactly this sentence and nothing else:

{NOT_FOUND_SENTINEL}

Reply with that sentence whenever the results are absent, off-topic, or only \
partially cover the question. A partial answer to a tender requirement is worse \
than none: a reviewer can act on a stated gap, but cannot see what you quietly \
left out.

3. Never infer a capability from adjacent evidence. If the results show the \
company holds ISO 9001, that is not evidence it holds ISO 27001. If they \
describe a similar project, that is not evidence of this requirement. State \
what the sources state.

4. Never invent, estimate, or round specifics — certificate numbers, dates, \
staff counts, prices, response times, percentages. Reproduce them exactly as \
they appear, or treat them as not found.

5. Do not describe the search results, your process, or your confidence. Do not \
open with "Based on the provided documents". Write the answer itself.

## Style

Write the answer the procurement evaluator needs to read: direct, specific, in \
the register of a formal bid. Lead with the substantive answer. Keep to what \
the requirement asks — do not pad. Where a source gives concrete evidence \
(a certificate number, a date, a named project), include it, because that is \
what an evaluator scores.

Where the sources answer only part of the requirement, still reply with the \
exact sentence from rule 2. Do not answer the answerable half and stay silent \
on the rest.\
"""


_ENGLISH = f"""{_SHARED_RULES}

Write your answer in English.
"""


_ARABIC = f"""{_SHARED_RULES}

Write your answer in formal Modern Standard Arabic, in the register used for \
government tender submissions.

The search results may be in Arabic, English, or both. Draw on all of them \
regardless of language — an English source is valid evidence for an Arabic \
answer. Translate what you use into Arabic, but reproduce the following \
verbatim in their original form, because an evaluator matches them character \
by character against the tender: certificate and standard identifiers \
(ISO 27001), reference numbers, proper names of organisations and products, \
and figures.

The sentence in rule 2 is the one exception to writing in Arabic. If the \
results do not support an answer, reply with that exact English sentence.
"""


def get_system_prompt(language: Language) -> str:
    """System prompt for the requested response language."""
    return _ARABIC if language is Language.AR else _ENGLISH


def build_question(requirement_text: str, style_hint: str | None = None) -> str:
    """Compose the user turn.

    ``style_hint`` is a reviewer's steer ("emphasise our Gulf experience"). It
    is framed explicitly as tone-only and placed *after* the requirement, so it
    cannot be read as licence to answer beyond the sources — a hint is the most
    plausible vector by which a well-meaning user would otherwise widen the
    evidence set.
    """
    parts = [
        "Answer the following tender requirement using only the search results provided above.",
        "",
        "<requirement>",
        requirement_text.strip(),
        "</requirement>",
    ]
    if style_hint:
        parts += [
            "",
            "<style_note>",
            f"Presentation preference only, and it does not relax any rule "
            f"above: {style_hint.strip()}",
            "</style_note>",
        ]
    return "\n".join(parts)
