"""Document upload and ingestion-status endpoints.

Upload is **always** asynchronous. A 400-page scanned Arabic tender takes
minutes of OCR, which is far past any reasonable HTTP timeout, so the endpoint's
job is to get the bytes safely into object storage, record a row, enqueue the
work, and return 202 with somewhere to poll. Parsing in-request would tie up a
worker for the duration and drop the upload on any timeout.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from app.api.v1.deps.auth import CurrentUser, require_upload_permission
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ChecksumMismatchError,
    FileTooLargeError,
    UnsupportedFormatError,
)
from app.db.models.enums import DocumentRole, DocumentStatus
from app.schemas.common import TaskAccepted
from app.schemas.document import ALLOWED_MIME_TYPES, DocumentStatusRead
from app.services.storage import ObjectStorage, build_storage_key, sha256_bytes

logger = logging.getLogger(__name__)

router = APIRouter(tags=["documents"])

#: Read in 1 MB slices so a malicious Content-Length cannot force a large
#: allocation before the size check runs.
_READ_CHUNK = 1024 * 1024

#: Magic-byte prefixes, checked against the declared content type. A client that
#: labels an executable as a PDF is either broken or hostile; either way the
#: parser should never see the bytes.
_MAGIC = {
    "application/pdf": (b"%PDF-",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (b"PK\x03\x04",),
    "application/msword": (b"\xd0\xcf\x11\xe0",),
    "application/vnd.ms-excel": (b"\xd0\xcf\x11\xe0", b"PK\x03\x04"),
}


@router.post(
    "/upload-document",
    response_model=TaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a tender or knowledge-base document for ingestion",
)
async def upload_document(
    file: Annotated[UploadFile, File(description="The document to ingest")],
    role: Annotated[DocumentRole, Form(description="How this document will be used")],
    workspace_id: Annotated[
        uuid.UUID | None,
        Form(description="Scope to one pursuit; omit for tenant-wide knowledge"),
    ] = None,
    expected_sha256: Annotated[
        str | None, Form(description="Optional client-computed digest")
    ] = None,
    user: CurrentUser = Depends(require_upload_permission),
    settings: Settings = Depends(get_settings),
) -> TaskAccepted:
    """Accept a document, store it, and queue ingestion.

    Returns 202 immediately. Poll ``GET /documents/{id}/status`` until
    ``is_terminal`` is true.
    """
    data = await _read_and_validate(file, settings)
    digest = sha256_bytes(data)

    if expected_sha256 and expected_sha256.lower() != digest:
        # Truncated or tampered upload. Refusing here matters because a partial
        # PDF frequently parses "successfully" into partial text, producing a
        # document that looks ingested but is missing content — and nothing
        # downstream can tell that it is incomplete.
        raise ChecksumMismatchError(
            "uploaded bytes do not match the declared digest",
            expected=expected_sha256.lower(),
            actual=digest,
        )

    document_id = uuid.uuid4()
    key = build_storage_key(str(user.tenant_id), str(document_id), file.filename or "document")

    ObjectStorage(settings).put(key, data, content_type=file.content_type or "")
    logger.info(
        "stored document %s for tenant %s (%d bytes, %s)",
        document_id,
        user.tenant_id,
        len(data),
        file.content_type,
    )

    # NOTE: the documents row is written and the Celery task enqueued by
    # DocumentService within one transaction, so a row can never exist without
    # queued work (a document stuck at UPLOADED forever) and work can never be
    # queued for a row that does not exist. Wiring lands with the service layer
    # in Phase 4; the contract is fixed here.
    task_id = f"ingest:{document_id}"

    return TaskAccepted(
        task_id=task_id,
        resource_id=document_id,
        status=DocumentStatus.UPLOADED.value,
        poll_url=f"/api/v1/documents/{document_id}/status",
    )


@router.get(
    "/documents/{document_id}/status",
    response_model=DocumentStatusRead,
    summary="Poll ingestion progress",
)
async def get_document_status(
    document_id: uuid.UUID,
    user: CurrentUser = Depends(require_upload_permission),
) -> DocumentStatusRead:
    """Report ingestion state.

    A row belonging to another tenant returns 404, not 403 — see
    ``TenantMismatchError`` for why that distinction matters.
    """
    raise NotImplementedError("wired to DocumentService in Phase 4")


# ----------------------------------------------------------------- internals
async def _read_and_validate(file: UploadFile, settings: Settings) -> bytes:
    """Read the upload, enforcing type and size before anything else touches it.

    Raises:
        UnsupportedFormatError: type not allowed, or bytes contradict the type.
        FileTooLargeError: exceeds the configured ceiling.
    """
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_MIME_TYPES:
        raise UnsupportedFormatError(
            f"content type {content_type!r} is not supported",
            content_type=content_type,
            allowed=sorted(ALLOWED_MIME_TYPES),
        )

    buffer = bytearray()
    while chunk := await file.read(_READ_CHUNK):
        buffer.extend(chunk)
        # Checked during the read, not after: Content-Length is client-supplied
        # and trusting it means a 10 GB body is fully buffered before rejection.
        if len(buffer) > settings.max_upload_bytes:
            raise FileTooLargeError(
                f"file exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit",
                limit_bytes=settings.max_upload_bytes,
            )

    data = bytes(buffer)
    if not data:
        raise UnsupportedFormatError("file is empty")

    expected_magic = _MAGIC.get(content_type)
    if expected_magic and not any(data.startswith(m) for m in expected_magic):
        raise UnsupportedFormatError(
            f"file contents do not match the declared type {content_type!r}",
            content_type=content_type,
        )

    return data
