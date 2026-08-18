"""Data access for tender workspaces."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.enums import Language, WorkspaceStatus
from app.db.models.workspace import Workspace


class WorkspaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        owner_id: uuid.UUID,
        name: str,
        response_language: Language,
        grounding_config: dict[str, object],
        description: str | None = None,
        tender_reference: str | None = None,
        issuing_authority: str | None = None,
        submission_deadline: date | None = None,
        estimated_value: int | None = None,
        currency: str | None = None,
    ) -> Workspace:
        workspace = Workspace(
            tenant_id=tenant_id,
            owner_id=owner_id,
            name=name,
            description=description,
            tender_reference=tender_reference,
            issuing_authority=issuing_authority,
            submission_deadline=submission_deadline,
            estimated_value=estimated_value,
            currency=currency,
            response_language=response_language,
            # Snapshotted at creation. A tenant tightening its policy next
            # quarter must not retroactively change the confidence figures a
            # reviewer already signed off on.
            grounding_config=grounding_config,
            status=WorkspaceStatus.DRAFT,
        )
        self._session.add(workspace)
        await self._session.flush()
        return workspace

    async def get(self, workspace_id: uuid.UUID) -> Workspace | None:
        """No tenant predicate: RLS already scopes the session."""
        return (
            await self._session.execute(
                select(Workspace).where(
                    Workspace.id == workspace_id,
                    Workspace.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def list(
        self,
        *,
        status: WorkspaceStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Workspace], int]:
        conditions = [Workspace.deleted_at.is_(None)]
        if status is not None:
            conditions.append(Workspace.status == status)

        total = (
            await self._session.execute(
                select(func.count()).select_from(Workspace).where(*conditions)
            )
        ).scalar_one()
        rows = (
            (
                await self._session.execute(
                    select(Workspace)
                    .where(*conditions)
                    # Newest first: a bid manager opens the pursuit they are
                    # working on now, not the one they closed last quarter.
                    .order_by(Workspace.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return list(rows), total

    async def refresh_counts(self, workspace: Workspace, *, approved: int, total: int) -> None:
        """Cache the review counts the dashboard reads.

        Denormalised deliberately: the tender list renders a progress bar per
        row, and computing it live would be one aggregate query per workspace
        on every page load. The authoritative count is still the one the export
        gate computes from the proposals themselves — this is a display value
        and nothing branches on it.
        """
        workspace.requirements_total = total
        workspace.requirements_approved = approved
