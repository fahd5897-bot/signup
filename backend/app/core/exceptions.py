"""Domain exceptions and their HTTP mapping.

Every ingestion failure is modelled as one of these rather than as a bare
``Exception``, for three reasons:

* the worker needs to know whether a failure is worth retrying (a corrupt file
  never is; an S3 timeout always is),
* the reviewer needs a message that says what to do about it, in their own
  language, and
* the API needs a stable machine-readable slug the frontend can branch on.

``is_retryable`` is the field the Celery task keys off. Getting it wrong in
either direction is expensive: retrying a corrupt PDF burns OCR budget forever,
while not retrying a transient S3 error loses a customer's upload.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every expected, handled failure."""

    #: Stable slug for the frontend. Never change one in place — add a new one.
    slug: str = "internal_error"
    status_code: int = 500
    is_retryable: bool = False
    #: Safe to show a tenant user. Must not leak paths, hostnames, or SQL.
    user_message: str = "An unexpected error occurred."

    def __init__(self, detail: str | None = None, **context: Any) -> None:
        self.detail = detail or self.user_message
        self.context = context
        super().__init__(self.detail)

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": self.slug,
            "title": self.user_message,
            "detail": self.detail,
            **({"context": self.context} if self.context else {}),
        }


# --------------------------------------------------------------- tenancy/auth
class TenantMismatchError(AppError):
    """A request touched a resource belonging to another tenant.

    This is 404, not 403. Returning 403 confirms the resource exists, which
    turns an ID-guessing attack into a working enumeration oracle across
    tenants.
    """

    slug = "not_found"
    status_code = 404
    user_message = "Resource not found."


class PermissionDeniedError(AppError):
    slug = "permission_denied"
    status_code = 403
    user_message = "You do not have permission to perform this action."


class QuotaExceededError(AppError):
    slug = "quota_exceeded"
    status_code = 402
    user_message = "Your plan's quota has been exhausted."


# ---------------------------------------------------------------------- review
class VersionConflictError(AppError):
    """The row changed between the reviewer loading it and saving.

    The review workbench is a stateless HTTP client: by the time an approval
    arrives, the answer on the reviewer's screen may be several revisions old.
    Applying the decision anyway would attach a human's sign-off to text they
    never read, which is precisely the failure the approval gate exists to
    prevent — so a stale version is refused, not merged.
    """

    slug = "version_conflict"
    status_code = 409
    user_message = "This answer changed since you opened it. Reload and review again."


class InvalidTransitionError(AppError):
    """The requested status is not reachable from the current one."""

    slug = "invalid_transition"
    status_code = 409
    user_message = "That action is not available for this answer's current state."


class UngroundedApprovalError(AppError):
    """Approval refused: the answer carries no resolvable evidence.

    The product's single hard promise is that nothing leaves the system without
    a citation tied to a real source in the customer's own documents. An answer
    with no citations can still be approved, but only by a reviewer who states
    in writing that they are vouching for it themselves — never by default.
    """

    slug = "approval_blocked"
    status_code = 422
    user_message = "This answer has no citations and cannot be approved as grounded."


# ------------------------------------------------------------------- upload
class UnsupportedFormatError(AppError):
    """Content type is not on the allowlist, or the bytes contradict it."""

    slug = "unsupported_format"
    status_code = 415
    is_retryable = False
    user_message = "This file type is not supported."


class FileTooLargeError(AppError):
    slug = "file_too_large"
    status_code = 413
    is_retryable = False
    user_message = "This file exceeds the maximum upload size."


class ChecksumMismatchError(AppError):
    """Bytes received do not hash to the digest the client declared.

    Truncated upload or a tampered request; either way the file must not be
    parsed, because a partial PDF often parses "successfully" into partial text.
    """

    slug = "checksum_mismatch"
    status_code = 400
    is_retryable = False
    user_message = "The uploaded file was incomplete or corrupted in transit."


class DuplicateDocumentError(AppError):
    slug = "duplicate_document"
    status_code = 409
    user_message = "This document has already been uploaded."


# ---------------------------------------------------------------- ingestion
class IngestionError(AppError):
    """Base for pipeline failures. Retryable unless a subclass says otherwise."""

    slug = "ingestion_failed"
    status_code = 500
    is_retryable = True
    user_message = "The document could not be processed."


class CorruptFileError(IngestionError):
    """The parser could not read the file at all.

    Never retried: a corrupt file is corrupt on every attempt, and hi_res OCR
    on a 400-page scan is expensive enough that a retry loop is a real cost.
    """

    slug = "corrupt_file"
    status_code = 422
    is_retryable = False
    user_message = "This file appears to be corrupted or password-protected."


class EncryptedFileError(CorruptFileError):
    slug = "encrypted_file"
    user_message = "This file is password-protected. Remove the password and re-upload."


class EmptyExtractionError(IngestionError):
    """Parsing succeeded but produced (almost) no text.

    The signature failure of a scanned Arabic PDF when OCR is misconfigured or
    the scan quality is too low. Treated as an error rather than an empty
    success, because an empty document that reports READY becomes an evidence
    source that silently contributes nothing — and the resulting weak answers
    look like a model problem, not an ingestion problem.
    """

    slug = "no_text_extracted"
    status_code = 422
    is_retryable = False
    user_message = (
        "No readable text could be extracted. If this is a scanned document, "
        "please upload a higher-quality scan."
    )


class ParserTimeoutError(IngestionError):
    slug = "parser_timeout"
    is_retryable = True
    user_message = "Processing timed out. It will be retried automatically."


class EmbeddingError(IngestionError):
    slug = "embedding_failed"
    is_retryable = True
    user_message = "Could not index the document. It will be retried automatically."


class VectorStoreError(IngestionError):
    slug = "vector_store_unavailable"
    is_retryable = True
    user_message = "The search index is temporarily unavailable."


class StorageError(IngestionError):
    slug = "storage_unavailable"
    is_retryable = True
    user_message = "File storage is temporarily unavailable."
