"""Tender workspaces — creating and listing pursuits."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, status

from app.api.v1.deps.auth import CurrentUser, get_current_user
from app.core.config import Settings, get_settings
from app.core.exceptions import PermissionDeniedError
from app.db.models.enums import UserRole, WorkspaceStatus
from app.schemas.common import Page
from app.schemas.workspace import WorkspaceCreate, WorkspaceRead
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workspaces"])


@router.post(
    "/workspaces",
    response_model=WorkspaceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new tender pursuit",
)
async def create_workspace(
    payload: WorkspaceCreate,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> WorkspaceRead:
    """Create a workspace owned by the caller.

    ``tenant_id`` and ``owner_id`` come from the verified token, never from the
    body — ``WorkspaceCreate`` does not expose either field, so a client cannot
    create a pursuit inside someone else's tenant or attribute it to another
    user.
    """
    if user.role not in (UserRole.OWNER, UserRole.BID_MANAGER):
        raise PermissionDeniedError("your role cannot start a tender pursuit")

    workspace = await WorkspaceService(settings).create(
        tenant_id=user.tenant_id, owner_id=user.id, payload=payload
    )
    return WorkspaceRead.model_validate(workspace)


@router.get(
    "/workspaces",
    response_model=Page[WorkspaceRead],
    summary="List the tenant's tender pursuits",
)
async def list_workspaces(
    status_filter: WorkspaceStatus | None = None,
    limit: int = 50,
    offset: int = 0,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> Page[WorkspaceRead]:
    workspaces, total = await WorkspaceService(settings).list(
        tenant_id=user.tenant_id,
        status=status_filter,
        limit=min(limit, 200),
        offset=offset,
    )
    return Page[WorkspaceRead](
        items=[WorkspaceRead.model_validate(w) for w in workspaces],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/workspaces/{workspace_id}",
    response_model=WorkspaceRead,
    summary="One pursuit",
)
async def get_workspace(
    workspace_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> WorkspaceRead:
    workspace = await WorkspaceService(settings).get(
        tenant_id=user.tenant_id, workspace_id=workspace_id
    )
    return WorkspaceRead.model_validate(workspace)
