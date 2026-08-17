"""Document upload and ingestion-status schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, computed_field, field_validator

from app.db.models.enums import DocumentRole, DocumentStatus, Language, ParseStrategy
from app.schemas.base import APIModel, StrictModel, TimestampedRead

#: Accepted upload types. An allowlist, not a blocklist — Unstructured.io will
#: cheerfully attempt many formats, but each one added here is a parser code
#: path that has to be tested against Arabic content before it ships.
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
        "application/msword",  # .doc
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
        "application/vnd.ms-excel",  # .xls
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
        "text/plain",
        "text/csv",
        "text/html",
    }
)

MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB — public tender packs are large


class DocumentUploadRequest(StrictModel):
    """Step 1 of the two-step upload: register metadata, receive a presigned URL.

    Bytes never transit the API. The client PUTs directly to object storage,
    which keeps 200 MB tender packs off the application's memory and request
    timeout budget.
    """

    filename: str = Field(min_length=1, max_length=512)
    mime_type: str
    size_bytes: int = Field(gt=0, le=MAX_UPLOAD_BYTES)
    #: Client-computed SHA-256, used for de-duplication *and* re-verified
    #: server-side after upload — a client-supplied digest is a hint, never a
    #: trusted fact.
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    role: DocumentRole
    #: NULL registers a tenant-wide knowledge-base document; set scopes it to
    #: one pursuit. See ``Document.workspace_id``.
    workspace_id: uuid.UUID | None = None

    @field_validator("mime_type")
    @classmethod
    def _check_mime(cls, v: str) -> str:
        if v not in ALLOWED_MIME_TYPES:
            raise ValueError(f"unsupported content type: {v}")
        return v

    @field_validator("filename")
    @classmethod
    def _sanitise_filename(cls, v: str) -> str:
        """Strip any path component before the name reaches a storage key."""
        cleaned = v.replace("\\", "/").split("/")[-1].strip()
        if not cleaned or cleaned in {".", ".."}:
            raise ValueError("invalid filename")
        return cleaned


class DocumentUploadResponse(APIModel):
    """Step 1 response — the client now PUTs the bytes to ``upload_url``."""

    document_id: uuid.UUID
    upload_url: str
    upload_expires_at: datetime
    #: True when an identical file already exists in this tenant. The client
    #: may skip the PUT entirely; the existing parse and embeddings are reused.
    is_duplicate: bool = False


class DocumentRead(TimestampedRead):
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID | None
    filename: str
    mime_type: str
    size_bytes: int
    role: DocumentRole
    status: DocumentStatus
    parse_strategy: ParseStrategy | None
    language: Language
    page_count: int | None
    chunk_count: int
    text_extraction_ratio: float | None
    parsed_at: datetime | None
    indexed_at: datetime | None
    failure_reason: str | None

    @computed_field
    @property
    def is_global_knowledge(self) -> bool:
        return self.workspace_id is None

    @computed_field
    @property
    def extraction_quality(self) -> str:
        """Traffic light for the document list.

        The most common Arabic failure mode is a scanned PDF that parses
        "successfully" into near-empty text. Surfacing the ratio as a warning
        is what stops a silently empty document from being trusted as evidence.
        """
        if self.text_extraction_ratio is None:
            return "unknown"
        if self.text_extraction_ratio >= 0.90:
            return "good"
        if self.text_extraction_ratio >= 0.60:
            return "degraded"
        return "poor"


class DocumentStatusRead(APIModel):
    """Lightweight polling projection for the upload progress indicator."""

    id: uuid.UUID
    status: DocumentStatus
    chunk_count: int
    failure_reason: str | None = None

    @computed_field
    @property
    def is_terminal(self) -> bool:
        """Tells the client to stop polling."""
        return self.status in (
            DocumentStatus.READY,
            DocumentStatus.FAILED,
            DocumentStatus.QUARANTINED,
        )


class DocumentFilter(StrictModel):
    """Query parameters for the document list endpoint."""

    workspace_id: uuid.UUID | None = None
    role: DocumentRole | None = None
    status: DocumentStatus | None = None
    language: Language | None = None
    search: str | None = Field(default=None, max_length=255)
