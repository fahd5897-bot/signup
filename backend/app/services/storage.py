"""Object storage adapter.

Keys are always ``{tenant_id}/{document_id}/{filename}``. The tenant prefix is
constructed here and nowhere else, so storage-level isolation has one owner in
the same way vector-level isolation does (see ``rag/vectorstore/filters.py``).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, BinaryIO

from app.core.config import Settings, get_settings
from app.core.exceptions import StorageError

logger = logging.getLogger(__name__)

#: Anything outside this set is replaced. Prevents a crafted filename from
#: escaping its prefix or confusing downstream tooling.
_UNSAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9._\-؀-ۿ]")

_PRESIGN_TTL = timedelta(minutes=30)


@dataclass(slots=True)
class StoredObject:
    key: str
    size_bytes: int
    sha256: str


def build_storage_key(tenant_id: str, document_id: str, filename: str) -> str:
    """Compose the canonical object key.

    The tenant ID leads, so a bucket policy or IAM condition can enforce
    isolation by prefix independently of application code.
    """
    safe = _UNSAFE_KEY_CHARS.sub("_", filename.strip())[:200] or "document"
    return f"{tenant_id}/{document_id}/{safe}"


class ObjectStorage:
    """Thin S3 wrapper. Any S3-compatible backend (MinIO, R2) works unchanged."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise StorageError(f"boto3 is not installed: {exc}") from exc
            self._client = boto3.client(
                "s3",
                endpoint_url=self._settings.s3_endpoint,
                aws_access_key_id=self._settings.s3_access_key.get_secret_value(),
                aws_secret_access_key=self._settings.s3_secret_key.get_secret_value(),
            )
        return self._client

    def put(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        try:
            self._get_client().put_object(
                Bucket=self._settings.s3_bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                # Server-side encryption at rest. Tender material is commercially
                # sensitive and frequently under an NDA with the issuing body.
                ServerSideEncryption="AES256",
            )
        except Exception as exc:
            raise StorageError(f"failed to store {key!r}: {exc}") from exc

        return StoredObject(key=key, size_bytes=len(data), sha256=sha256_bytes(data))

    def get(self, key: str) -> bytes:
        try:
            response = self._get_client().get_object(Bucket=self._settings.s3_bucket, Key=key)
            return response["Body"].read()
        except Exception as exc:
            raise StorageError(f"failed to read {key!r}: {exc}") from exc

    def delete(self, key: str) -> None:
        try:
            self._get_client().delete_object(Bucket=self._settings.s3_bucket, Key=key)
        except Exception as exc:
            raise StorageError(f"failed to delete {key!r}: {exc}") from exc

    def presigned_put_url(self, key: str, *, content_type: str) -> tuple[str, datetime]:
        """URL for a client-direct upload, bypassing the API entirely.

        Used for large tender packs: routing 200 MB through the application
        costs a worker slot for the duration of the client's upload and risks
        the request timeout, for no benefit.
        """
        try:
            url = self._get_client().generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._settings.s3_bucket,
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=int(_PRESIGN_TTL.total_seconds()),
            )
        except Exception as exc:
            raise StorageError(f"failed to presign {key!r}: {exc}") from exc
        return url, datetime.now(UTC) + _PRESIGN_TTL


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(stream: BinaryIO, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    """Hash a stream without holding it in memory. Returns (digest, size)."""
    digest = hashlib.sha256()
    total = 0
    while chunk := stream.read(chunk_size):
        digest.update(chunk)
        total += len(chunk)
    return digest.hexdigest(), total
