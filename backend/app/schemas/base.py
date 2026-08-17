"""Shared schema conventions.

Two base classes, and the distinction between them is a security boundary:

* :class:`APIModel` — anything *leaving* the API. Reads from ORM attributes.
* :class:`StrictModel` — anything *entering* the API. Rejects unknown fields.

``extra="forbid"`` on inbound payloads is what stops a client from smuggling
``{"tenant_id": "<someone else's>"}`` or ``{"status": "approved"}`` into a
create/update call and having it silently bind. Inbound schemas never expose
``tenant_id`` at all — it comes from the authenticated token, never the body.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    """Base for outbound (response) models."""

    model_config = ConfigDict(
        from_attributes=True,  # populate directly from ORM instances
        populate_by_name=True,
        ser_json_timedelta="iso8601",
        str_strip_whitespace=True,
    )


class StrictModel(BaseModel):
    """Base for inbound (request) models."""

    model_config = ConfigDict(
        extra="forbid",  # unknown keys are a 422, never ignored
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class TimestampedRead(APIModel):
    """Mixin for read models exposing the standard audit columns."""

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class PageParams(StrictModel):
    """Cursor-free pagination. Adequate to a few thousand rows per tenant;
    swap for keyset pagination if a tenant's document list outgrows it.
    """

    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
