"""Prompt and tool schema for pulling requirements out of a tender document.

Extraction has the opposite failure mode from generation. There, the danger is
inventing an answer; here, it is **missing a requirement**. A fabricated
requirement is caught downstream — it has no verbatim source span, so the chain
drops it. A missed one is invisible: the compliance matrix simply does not
mention it, the export gate sees nothing outstanding, and the bid is
disqualified on a clause nobody read.

So the instructions push toward over-inclusion and the verification step
afterwards prunes. The model is also told never to paraphrase the source span,
because that span is what proves the requirement is real.
"""

from __future__ import annotations

from typing import Any, Final

from app.db.models.enums import Language

PROMPT_VERSION: Final = "requirement-extraction/2026-08-18"

#: Name of the tool the model must call. Response text is ignored entirely —
#: a schema-constrained tool call is the only accepted output shape, so a
#: chatty preamble cannot be mistaken for data.
TOOL_NAME: Final = "record_requirements"

REQUIREMENT_TOOL: Final[dict[str, Any]] = {
    "name": TOOL_NAME,
    "description": (
        "Record every requirement, question, or obligation found in this "
        "extract of a tender document. Call once with all of them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "requirements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "requirement_ref": {
                            "type": "string",
                            "description": (
                                "The reference exactly as printed in the document "
                                "('3.2.14', 'Annex B Q7', 'المادة ٥'). Empty string "
                                "if the document does not number this item."
                            ),
                        },
                        "requirement_text": {
                            "type": "string",
                            "description": (
                                "The requirement stated as a self-contained question "
                                "or obligation, in the document's own language."
                            ),
                        },
                        "source_text": {
                            "type": "string",
                            "description": (
                                "A span copied CHARACTER FOR CHARACTER from the "
                                "extract above. Do not paraphrase, translate, "
                                "re-punctuate, or fix typos. This is checked "
                                "against the document and the requirement is "
                                "discarded if it does not match."
                            ),
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "technical",
                                "financial",
                                "legal",
                                "administrative",
                                "hse",
                                "other",
                            ],
                        },
                        "is_mandatory": {
                            "type": "boolean",
                            "description": (
                                "True when compliance is obligatory ('shall', "
                                "'must', 'required', 'يجب', 'إلزامي'). When in "
                                "doubt, say true."
                            ),
                        },
                        "section_path": {
                            "type": "string",
                            "description": "Heading breadcrumb, or empty string.",
                        },
                    },
                    "required": [
                        "requirement_ref",
                        "requirement_text",
                        "source_text",
                        "category",
                        "is_mandatory",
                    ],
                },
            }
        },
        "required": ["requirements"],
    },
}


_SHARED = """\
You are reading an extract from a public tender document (RFP/RFQ/ITT) on \
behalf of a company preparing a bid. Your job is to find every item the bidder \
must respond to or comply with.

## What counts as a requirement

* Direct questions to the bidder.
* Obligations and conditions of contract ("the contractor shall...").
* Mandatory submittals: certificates, licences, financial statements, samples.
* Evaluation criteria the bidder is scored against.
* Rows of a compliance, pricing, or bill-of-quantities table where each row \
demands a response.

## Rules

1. `source_text` must be copied character for character from the extract. \
Never paraphrase it, never translate it, never tidy its punctuation. It is \
checked against the document, and a requirement whose span cannot be found is \
discarded.
2. `requirement_text` is different: state the requirement clearly enough to \
stand alone, in the language of the document. A reader who cannot see the \
tender must understand what is being asked.
3. Use the reference exactly as printed. Do not invent numbering, and do not \
renumber. If an item is unnumbered, return an empty string.
4. Prefer including a doubtful item over omitting it. A requirement that turns \
out to be background text costs a reviewer ten seconds; one that is missed \
costs the bid.
5. Boilerplate that asks nothing of the bidder — definitions, the covering \
letter, table-of-contents lines — is not a requirement.
6. Report nothing when the extract contains no requirements. An empty list is \
a valid and useful answer; padding it is not.
7. Return everything through the `record_requirements` tool. Do not answer in \
prose.
"""

_ARABIC_NOTE = """\

## Arabic documents

Copy Arabic `source_text` exactly as it appears, including its original \
spelling, diacritics, and digit forms (٠-٩ or 0-9 as printed). Do not \
normalise it — the span is matched against the document, and a "corrected" \
copy will not be found.

Arabic tenders often state obligations with يجب / يلتزم / على المورد / \
إلزامي. Treat these as mandatory. Numbered clauses appear as المادة / البند / \
الفقرة followed by a number; that whole label is the reference.
"""


def get_system_prompt(language: Language) -> str:
    """System prompt, with an extra section for Arabic sources."""
    if language is Language.AR:
        return _SHARED + _ARABIC_NOTE
    return _SHARED


def build_extract_message(extract: str, *, window_index: int, window_count: int) -> str:
    """User turn for one window of the document.

    The window position is stated because a requirement can be cut in half by
    the window boundary; telling the model where it is stops it from
    speculating about text it cannot see.
    """
    position = f"Extract {window_index + 1} of {window_count}."
    return (
        f"{position} Find every requirement in the extract below and record "
        f"them with the `{TOOL_NAME}` tool.\n\n"
        "If a requirement is cut off at the start or end of the extract, "
        "record only what is fully visible; the adjacent extract covers the "
        "rest.\n\n"
        f"<extract>\n{extract}\n</extract>"
    )


__all__ = [
    "PROMPT_VERSION",
    "REQUIREMENT_TOOL",
    "TOOL_NAME",
    "build_extract_message",
    "get_system_prompt",
]
