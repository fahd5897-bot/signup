"""Generic envelopes reused across routers."""

from __future__ import annotations

import uuid
from typing import Generic, TypeVar

from pydantic import Field

from app.schemas.base import APIModel

T = TypeVar("T")


class Page(APIModel, Generic[T]):  # noqa: UP046 - PEP 695 syntax breaks pydantic generic ser
    """Paginated collection response."""

    items: list[T]
    total: int = Field(description="Total rows matching the filter, ignoring paging")
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class TaskAccepted(APIModel):
    """202 response for work handed to Celery.

    Ingestion and generation are always asynchronous — a 400-page Arabic tender
    takes minutes to OCR, far past any sensible HTTP timeout. Clients poll the
    resource or subscribe to its SSE stream.
    """

    task_id: str
    resource_id: uuid.UUID
    status: str
    poll_url: str


class ErrorDetail(APIModel):
    """RFC 9457-shaped error body, emitted by the global exception handler."""

    type: str = Field(description="Stable machine-readable error slug")
    title: str
    detail: str | None = None
    #: Present on 422 so the UI can attach messages to the offending field.
    errors: list[dict[str, object]] | None = None
