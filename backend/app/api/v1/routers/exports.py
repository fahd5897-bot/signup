"""Export endpoints — the last thing that happens before a bid is submitted.

The response is the file itself rather than a link. An export is generated from
live data at the moment it is requested; handing back a URL invites the file to
be fetched later, after answers have changed, and a stale submission document is
worse than no document.
"""

from __future__ import annotations

import logging
import urllib.parse
import uuid

from fastapi import APIRouter, Depends, Response

from app.api.v1.deps.auth import CurrentUser, get_current_user
from app.core.config import Settings, get_settings
from app.core.exceptions import PermissionDeniedError
from app.db.models.enums import UserRole
from app.schemas.base import APIModel
from app.services.export_service import ExportFormat, ExportService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["exports"])


class ExportPreview(APIModel):
    """What an export would contain, without producing it.

    Runs the same gate the export runs, so the UI can disable the button for
    the same reason the API would refuse rather than letting a bid manager
    discover the blocker after clicking.
    """

    requirements: int
    answered: int
    mandatory: int
    #: Answers going out with no citation. Every one was approved by a named
    #: human who took explicit responsibility, and the count is surfaced so
    #: nobody has to take that on trust.
    uncited: int
    exported: int


@router.get(
    "/workspaces/{workspace_id}/export-preview",
    response_model=ExportPreview,
    summary="Check what an export would contain",
)
async def export_preview(
    workspace_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ExportPreview:
    summary = await ExportService(settings).preview(
        tenant_id=user.tenant_id, workspace_id=workspace_id
    )
    return ExportPreview(**summary)


@router.get(
    "/workspaces/{workspace_id}/export",
    summary="Download the submission document, PDF, or compliance matrix",
    response_class=Response,
    responses={
        200: {"content": {"application/octet-stream": {}}, "description": "The file"},
        409: {"description": "A mandatory requirement is not approved"},
    },
)
async def export_workspace(
    workspace_id: uuid.UUID,
    export_format: ExportFormat = ExportFormat.DOCX,
    user: CurrentUser = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Produce the artefact and mark the answers in it as exported.

    Restricted to owners and bid managers. There is deliberately no override
    parameter for the approval gate: an escape hatch would be used under
    deadline pressure, which is precisely when the check matters.
    """
    if user.role not in (UserRole.OWNER, UserRole.BID_MANAGER):
        raise PermissionDeniedError("your role cannot export a submission")

    artifact = await ExportService(settings).export(
        tenant_id=user.tenant_id,
        workspace_id=workspace_id,
        export_format=export_format,
    )

    logger.info(
        "export downloaded: workspace=%s format=%s by=%s",
        workspace_id,
        export_format.value,
        user.id,
    )

    return Response(
        content=artifact.content,
        media_type=artifact.media_type,
        headers={
            # Both forms: `filename` for clients that ignore RFC 5987, and
            # `filename*` for the rest. The name is ASCII-safe either way, but
            # the encoded form keeps it intact through proxies that re-encode.
            "Content-Disposition": (
                f'attachment; filename="{artifact.filename}"; '
                f"filename*=UTF-8''{urllib.parse.quote(artifact.filename)}"
            ),
            # The file is generated from live data and marks rows as exported;
            # a cached copy would be a stale submission document.
            "Cache-Control": "no-store",
            "X-Export-Answered": str(artifact.summary.get("answered", 0)),
            "X-Export-Uncited": str(artifact.summary.get("uncited", 0)),
        },
    )
