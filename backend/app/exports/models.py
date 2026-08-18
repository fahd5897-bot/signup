"""The shape every renderer consumes.

A plain dataclass rather than the ORM row, for one reason: renderers must not
be able to touch a proposal that has not been through the export gate. Building
this object is the gate's job, so a renderer physically cannot reach into the
database and pick up a draft.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.db.models.enums import Language, ProposalStatus


@dataclass(slots=True)
class ExportRow:
    """One requirement as it appears in the submission and its audit trail."""

    requirement_ref: str
    requirement_text: str
    is_mandatory: bool
    status: ProposalStatus
    section_path: str | None = None
    #: ``edited_text or answer_text`` — what actually ships. None for a
    #: requirement that was never answered; such a row appears in the audit
    #: matrix with its true status and never in the response document.
    answer_text: str | None = None
    language: Language = Language.EN
    #: Distinct source document names drawn from the answer's citations. This
    #: column is what a procurement authority audits: every claim traced to a
    #: document the bidder can produce on request.
    source_documents: list[str] = field(default_factory=list)
    citation_count: int = 0
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    was_edited_by_human: bool = False
    confidence_score: float | None = None

    @property
    def is_answered(self) -> bool:
        return bool(self.answer_text and self.answer_text.strip())


@dataclass(slots=True)
class ExportBundle:
    """Everything a renderer needs, with no access to anything else."""

    workspace_name: str
    tenant_name: str
    language: Language
    rows: list[ExportRow]
    generated_at: datetime

    @property
    def is_rtl(self) -> bool:
        return self.language is Language.AR

    @property
    def answered_rows(self) -> list[ExportRow]:
        """Rows that belong in the response document.

        Everything else — unanswered optional items, rejected drafts — appears
        in the audit matrix with its real status and nowhere else. Putting an
        unapproved draft into the submitted document is the exact failure the
        whole review gate exists to prevent.
        """
        return [r for r in self.rows if r.is_answered]


__all__ = ["ExportBundle", "ExportRow"]
