"""Export: the last gate, and the only one that produces a file.

The golden rule of this product is that nothing is submitted without a citation
tied to a real source in the customer's documents and without human approval.
Every earlier gate can be argued with — a reviewer can override an uncited
answer in writing, a low retrieval score can be answered manually. This one
cannot, because it is the point at which text leaves the system.

Two refusals, and neither takes a force flag:

* **Not every mandatory requirement is approved.** There is deliberately no
  override parameter. An escape hatch here would be used under deadline
  pressure, which is exactly when the check matters, and its existence would
  make the guarantee conditional rather than absolute.
* **A row that is not approved never reaches a renderer.** Enforced by
  construction: renderers consume :class:`~app.exports.models.ExportBundle`,
  which only this module builds, so no rendering path can reach into the
  database and pick up a draft.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, TenantMismatchError
from app.db.models.enums import Language, ProposalStatus
from app.db.models.proposal import GeneratedProposal
from app.db.models.user import User
from app.db.models.workspace import Workspace
from app.db.repositories.proposals import ProposalRepository
from app.db.session import tenant_session
from app.exports.compliance_matrix import MEDIA_TYPE as XLSX_MEDIA_TYPE
from app.exports.compliance_matrix import build_compliance_matrix, summarise
from app.exports.models import ExportBundle, ExportRow
from app.exports.pdf import MEDIA_TYPE as PDF_MEDIA_TYPE
from app.exports.pdf import build_pdf
from app.exports.response_document import MEDIA_TYPE as DOCX_MEDIA_TYPE
from app.exports.response_document import build_response_docx

logger = logging.getLogger(__name__)


class ExportFormat(StrEnum):
    DOCX = "docx"
    PDF = "pdf"
    MATRIX = "matrix"


class ExportBlockedError(AppError):
    """Mandatory requirements are still unapproved.

    The context carries the outstanding count and the first few references, so
    the bid manager is told what to go and approve instead of being told no.
    """

    slug = "export_blocked"
    status_code = 409
    user_message = "Every mandatory requirement must be approved before export."


@dataclass(slots=True)
class ExportArtifact:
    filename: str
    media_type: str
    content: bytes
    #: Counts shown to the bid manager as a receipt: how many requirements went
    #: out, how many of them carry no citation.
    summary: dict[str, int]


class ExportService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def export(
        self,
        *,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        export_format: ExportFormat,
        mark_exported: bool = True,
    ) -> ExportArtifact:
        """Produce one submission artefact.

        Raises:
            TenantMismatchError: no such workspace for this tenant.
            ExportBlockedError: a mandatory requirement is still unapproved.
            ArabicFontMissingError: PDF requested for Arabic with no font.
        """
        bundle, exportable_ids = await self._collect(tenant_id, workspace_id)

        if export_format is ExportFormat.MATRIX:
            content = build_compliance_matrix(bundle)
            media_type, extension = XLSX_MEDIA_TYPE, "xlsx"
        elif export_format is ExportFormat.DOCX:
            content = build_response_docx(bundle)
            media_type, extension = DOCX_MEDIA_TYPE, "docx"
        else:
            content = build_pdf(bundle)
            media_type, extension = PDF_MEDIA_TYPE, "pdf"

        # After the bytes exist, never before. Marking first and failing to
        # render would leave answers flagged as submitted that were not.
        if mark_exported and exportable_ids:
            await self._mark_exported(tenant_id, exportable_ids)

        filename = _filename(bundle.workspace_name, extension, bundle.generated_at)
        logger.info(
            "exported workspace %s as %s (%d answers)",
            workspace_id,
            export_format.value,
            len(bundle.answered_rows),
        )
        return ExportArtifact(
            filename=filename,
            media_type=media_type,
            content=content,
            summary=summarise(bundle),
        )

    async def preview(self, *, tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> dict[str, int]:
        """What an export would contain, without producing or marking anything.

        Runs the same gate, so the UI can disable the button for the same
        reason the API would refuse — rather than letting a bid manager
        discover the blocker after clicking export.
        """
        bundle, _ = await self._collect(tenant_id, workspace_id)
        return summarise(bundle)

    # ------------------------------------------------------------- internals
    async def _collect(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> tuple[ExportBundle, list[uuid.UUID]]:
        """Run the gate, then build the bundle. In that order."""
        async with tenant_session(tenant_id, self._settings) as session:
            workspace = (
                await session.execute(
                    select(Workspace).where(
                        Workspace.id == workspace_id, Workspace.deleted_at.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if workspace is None:
                raise TenantMismatchError("workspace not found")

            repository = ProposalRepository(session)
            approved, total = await repository.count_approved(workspace_id)
            if total == 0:
                # `total` counts mandatory requirements only, so zero means one
                # of two different things and the reviewer needs to be told
                # which: nothing has been extracted yet, or the tender turned
                # out to have no mandatory items at all. Reporting the second
                # as "no requirements" sends someone hunting for a matrix that
                # is sitting right in front of them.
                current, _ = await repository.list_current(workspace_id=workspace_id, limit=1)
                raise ExportBlockedError(
                    "this tender has no mandatory requirements, so there is "
                    "nothing the approval gate can certify"
                    if current
                    else "this workspace has no requirements yet — read the tender document first",
                    outstanding=0,
                    total=0,
                    has_optional_requirements=bool(current),
                )
            if approved < total:
                blocking = await self._blocking_refs(session, workspace_id)
                raise ExportBlockedError(
                    f"{total - approved} of {total} mandatory requirements are not approved",
                    outstanding=total - approved,
                    total=total,
                    examples=blocking,
                )

            proposals, _ = await repository.list_current(workspace_id=workspace_id, limit=10_000)
            reviewers = await _reviewer_names(session, proposals)
            tenant_name = await _tenant_name(session, tenant_id)

        rows = [_to_row(p, reviewers) for p in proposals]
        bundle = ExportBundle(
            workspace_name=workspace.name,
            tenant_name=tenant_name,
            language=workspace.response_language or Language.EN,
            rows=rows,
            generated_at=datetime.now(UTC),
        )
        exportable = [p.id for p in proposals if p.status is ProposalStatus.APPROVED]
        return bundle, exportable

    async def _blocking_refs(self, session, workspace_id: uuid.UUID) -> list[str]:
        """The first few unapproved mandatory references, for the error body."""
        rows = (
            (
                await session.execute(
                    select(GeneratedProposal.requirement_ref)
                    .where(
                        GeneratedProposal.workspace_id == workspace_id,
                        GeneratedProposal.is_current.is_(True),
                        GeneratedProposal.deleted_at.is_(None),
                        GeneratedProposal.is_mandatory.is_(True),
                        GeneratedProposal.status != ProposalStatus.APPROVED,
                        GeneratedProposal.status != ProposalStatus.EXPORTED,
                    )
                    .order_by(GeneratedProposal.requirement_ref)
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def _mark_exported(self, tenant_id: uuid.UUID, ids: list[uuid.UUID]) -> None:
        """Move approved answers to EXPORTED.

        The reviewer fields are left untouched: the export did not review
        anything, and overwriting the approver with whoever pressed the button
        would destroy the accountability the gate exists to record.
        """
        async with tenant_session(tenant_id, self._settings) as session:
            rows = (
                (
                    await session.execute(
                        select(GeneratedProposal).where(
                            GeneratedProposal.id.in_(ids),
                            GeneratedProposal.status == ProposalStatus.APPROVED,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for proposal in rows:
                proposal.status = ProposalStatus.EXPORTED
                # The row changed, so any reviewer holding the old revision
                # must be told rather than allowed to write over it.
                proposal.version = proposal.version + 1


def _to_row(proposal: GeneratedProposal, reviewers: dict[uuid.UUID, str]) -> ExportRow:
    final_text = proposal.edited_text or proposal.answer_text
    shipped = proposal.status in (ProposalStatus.APPROVED, ProposalStatus.EXPORTED)
    citations = proposal.citations or []
    return ExportRow(
        requirement_ref=proposal.requirement_ref,
        requirement_text=proposal.requirement_text,
        is_mandatory=proposal.is_mandatory,
        status=proposal.status,
        section_path=proposal.section_path,
        # Unapproved text is dropped here, not in the renderers. The audit
        # matrix still shows the row and its true status; what it must never
        # show is wording nobody signed off.
        answer_text=final_text if shipped else None,
        language=proposal.language,
        source_documents=_source_documents(citations),
        citation_count=len(citations),
        reviewer=reviewers.get(proposal.reviewed_by_id) if proposal.reviewed_by_id else None,
        reviewed_at=proposal.reviewed_at,
        was_edited_by_human=bool(
            proposal.edited_text and proposal.edited_text != proposal.answer_text
        ),
        confidence_score=proposal.confidence_score,
    )


def _source_documents(citations: list[dict]) -> list[str]:
    """Distinct source names, in first-cited order.

    Order matters more than it looks: an auditor reading the matrix alongside
    the answer expects the first-named document to be the one the opening
    claim came from.
    """
    seen: list[str] = []
    for citation in citations:
        name = str(citation.get("document_name") or "").strip()
        if name and name not in seen:
            seen.append(name)
    return seen


async def _reviewer_names(session, proposals: list[GeneratedProposal]) -> dict[uuid.UUID, str]:
    """Resolve reviewer ids to names in one query rather than per row."""
    ids = {p.reviewed_by_id for p in proposals if p.reviewed_by_id}
    if not ids:
        return {}
    rows = (
        await session.execute(select(User.id, User.full_name, User.email).where(User.id.in_(ids)))
    ).all()
    return {row.id: (row.full_name or row.email) for row in rows}


async def _tenant_name(session, tenant_id: uuid.UUID) -> str:
    from app.db.models.tenant import Tenant

    name = (
        await session.execute(select(Tenant.name).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    return name or ""


def _filename(workspace_name: str, extension: str, generated_at: datetime) -> str:
    """A filename that survives a download folder and a zip.

    Non-ASCII is stripped rather than transliterated: an Arabic workspace name
    round-trips badly through Content-Disposition and through some evaluators'
    file systems, and a mangled filename on a submitted document looks careless.
    The readable name is inside the document.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in workspace_name.strip())
    safe = "-".join(filter(None, safe.split("-")))[:60] or "tender-response"
    return f"{safe}-{generated_at.strftime('%Y%m%d-%H%M')}.{extension}"


__all__ = ["ExportArtifact", "ExportBlockedError", "ExportFormat", "ExportService"]
