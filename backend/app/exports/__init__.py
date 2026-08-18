"""Export artefacts: what the customer actually submits.

Three renderers, one rule between them — nothing unapproved is ever written
into a file. The gate lives in :mod:`app.services.export_service`; these
modules receive rows that have already passed it and are responsible only for
producing correct, readable, right-to-left-aware documents.
"""

from __future__ import annotations

from app.exports.compliance_matrix import build_compliance_matrix
from app.exports.pdf import ArabicFontMissingError, build_pdf
from app.exports.response_document import build_response_docx

__all__ = [
    "ArabicFontMissingError",
    "build_compliance_matrix",
    "build_pdf",
    "build_response_docx",
]
