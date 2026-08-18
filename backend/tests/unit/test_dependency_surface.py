"""The parsing extras must match the formats the API accepts.

`unstructured[all-docs]` is the tempting choice and the wrong one. It pulls the
`audio` extra, whose openai-whisper ships only an sdist whose setup.py imports
`pkg_resources` — removed from setuptools 81 — so the whole install fails on
Python 3.12. That broke CI and would have broken the runtime image build, for
a format this product never accepts.

Pinning it in a comment would rot the first time someone needs a new format.
This is the version that fails loudly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from app.schemas.document import ALLOWED_MIME_TYPES

REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements.txt"

#: Extras that exist only to support formats the upload endpoint refuses.
#: Each one costs install time and image size; `audio` also costs a build that
#: cannot succeed.
FORBIDDEN_EXTRAS = {"all-docs", "audio", "image", "epub", "odt", "org", "rst", "rtf"}

#: The extra `unstructured` publishes for each content type we accept. Types
#: handled by the core package (plain text, HTML) map to nothing.
EXTRA_FOR_MIME = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/csv": "csv",
    "text/plain": None,
    "text/html": None,
}


def _declared_extras() -> set[str]:
    match = re.search(r"^unstructured\[([^\]]+)\]==", REQUIREMENTS.read_text(), re.MULTILINE)
    assert match, "unstructured must be pinned with an explicit extras list"
    return {e.strip() for e in match.group(1).split(",")}


def test_no_extra_is_installed_for_a_format_we_refuse() -> None:
    assert not (_declared_extras() & FORBIDDEN_EXTRAS)


@pytest.mark.parametrize("mime", sorted(ALLOWED_MIME_TYPES))
def test_every_accepted_format_has_its_parser_installed(mime: str) -> None:
    """The failure this catches is silent in the other direction.

    A format on the allowlist with no extra installed passes upload validation
    and then fails deep in the parser, after the bytes are stored and the job
    is queued — surfacing to the customer as a corrupt file rather than an
    unsupported one.
    """
    assert mime in EXTRA_FOR_MIME, (
        f"{mime} was added to ALLOWED_MIME_TYPES without deciding which "
        "unstructured extra parses it"
    )
    required = EXTRA_FOR_MIME[mime]
    if required is None:
        return
    assert required in _declared_extras()
