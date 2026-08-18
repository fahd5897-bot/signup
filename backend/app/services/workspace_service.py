"""Tender workspaces: one pursuit, from upload to submission."""

from __future__ import annotations

import logging
import uuid

from app.core.config import Settings, get_settings
from app.core.exceptions import TenantMismatchError
from app.db.models.enums import WorkspaceStatus
from app.db.models.workspace import Workspace
from app.db.repositories.proposals import ProposalRepository
from app.db.repositories.workspaces import WorkspaceRepository
from app.db.session import tenant_session
from app.schemas.workspace import WorkspaceCreate

logger = logging.getLogger(__name__)


class WorkspaceService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def create(
        self, *, tenant_id: uuid.UUID, owner_id: uuid.UUID, payload: WorkspaceCreate
    ) -> Workspace:
        async with tenant_session(tenant_id, self._settings) as session:
            workspace = await WorkspaceRepository(session).create(
                tenant_id=tenant_id,
                owner_id=owner_id,
                name=payload.name.strip(),
                description=payload.description,
                tender_reference=payload.tender_reference,
                issuing_authority=payload.issuing_authority,
                submission_deadline=payload.submission_deadline,
                estimated_value=payload.estimated_value,
                currency=payload.currency,
                response_language=payload.response_language,
                grounding_config=payload.grounding_config.model_dump(mode="json"),
            )
            logger.info("workspace %s created for tenant %s", workspace.id, tenant_id)
            return workspace

    async def get(self, *, tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> Workspace:
        async with tenant_session(tenant_id, self._settings) as session:
            workspace = await WorkspaceRepository(session).get(workspace_id)
            if workspace is None:
                # 404 rather than 403: under RLS the row is genuinely invisible,
                # and confirming it exists would make this an enumeration
                # oracle across tenants.
                raise TenantMismatchError("workspace not found")
            return workspace

    async def list(
        self,
        *,
        tenant_id: uuid.UUID,
        status: WorkspaceStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Workspace], int]:
        """The tender list, with each row's review counts computed live.

        Recomputed rather than trusted from the cached columns, because every
        write path that could move them (approval, export, re-extraction) would
        otherwise have to remember to update two places, and the one that
        forgets shows a reviewer a progress bar that disagrees with the export
        gate.

        Each row is detached before its counts are set, so this stays a read.
        Assigning to a session-bound instance emits an UPDATE on a GET, and that
        UPDATE makes ``updated_at`` server-computed — which the response model
        then cannot read once the session has closed. The failure is
        intermittent in the worst way: it appears only when the counts actually
        changed, so the list works right up until someone approves something.
        """
        async with tenant_session(tenant_id, self._settings) as session:
            repository = WorkspaceRepository(session)
            workspaces, total = await repository.list(status=status, limit=limit, offset=offset)
            proposals = ProposalRepository(session)
            counts = [
                (workspace, await proposals.count_approved(workspace.id))
                for workspace in workspaces
            ]
            for workspace, (approved, mandatory_total) in counts:
                session.expunge(workspace)
                workspace.requirements_approved = approved
                workspace.requirements_total = mandatory_total
            return workspaces, total


__all__ = ["WorkspaceService"]
