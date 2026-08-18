"""Anthropic model identifiers, in one place.

Model IDs are never written inline anywhere else in the codebase. A string
literal scattered across call sites is how a fleet ends up half-migrated: some
routes on the new model, some on the old, and no single place that tells you
which.

**Verified against the installed SDK**, not from memory — `anthropic.types.Model`
enumerates the accepted identifiers, and all three below appear in it. Re-check
against the current model list before any migration; Anthropic retires older
models and adds new ones, and an ID that was valid last quarter may not be.

Tier rationale:

* **Generation** runs on the Opus tier. It is the call that must notice when
  retrieved evidence is adjacent-but-insufficient and abstain — the judgement
  the whole zero-hallucination guarantee rests on. This is the wrong place to
  economise.
* **Extraction** runs on Haiku. Pulling requirement rows out of a tender is
  high-volume, structurally simple, and gets a schema-constrained response, so
  the cheapest capable tier is the right one.
"""

from __future__ import annotations

from typing import Final

#: Grounded answer generation. Opus tier — 1M context, strongest judgement.
MODEL_GENERATION: Final = "claude-opus-5"

#: Requirement extraction, classification, routing. Cheap and high-volume.
MODEL_EXTRACTION: Final = "claude-haiku-4-5"

#: Alternative generation model, one line away.
#:
#: Set ``LLM_MODEL_GENERATION=claude-opus-4-8`` in the environment to switch
#: without touching code. Kept named here so the choice is discoverable rather
#: than buried in a deployment variable.
MODEL_GENERATION_ALTERNATE: Final = "claude-opus-4-8"

#: Every identifier this application is allowed to send. A typo in an env var
#: is caught at startup by the validator in ``app.core.config`` rather than at
#: the first API call, which on the generation path could be hours later.
ALLOWED_MODELS: Final[frozenset[str]] = frozenset(
    {
        MODEL_GENERATION,
        MODEL_GENERATION_ALTERNATE,
        MODEL_EXTRACTION,
    }
)
