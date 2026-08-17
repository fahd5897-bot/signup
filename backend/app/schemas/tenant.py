"""Tenant request/response schemas."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import Field, field_validator

from app.db.models.enums import Language, TenantPlan
from app.schemas.base import APIModel, StrictModel, TimestampedRead

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

Slug = Annotated[str, Field(min_length=3, max_length=63)]


class TenantCreate(StrictModel):
    name: str = Field(min_length=2, max_length=255)
    slug: Slug
    plan: TenantPlan = TenantPlan.TRIAL
    default_locale: Language = Language.EN

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        """Slugs appear in S3 key prefixes and public URLs.

        Rejecting anything but lower-case kebab-case here keeps path traversal
        and homograph tricks out of the storage layer entirely, rather than
        relying on downstream escaping.
        """
        v = v.lower()
        if not SLUG_RE.match(v):
            raise ValueError("slug must be lower-case alphanumeric words separated by hyphens")
        if v in {"api", "admin", "www", "static", "health", "auth"}:
            raise ValueError("slug is reserved")
        return v


class TenantUpdate(StrictModel):
    """PATCH body. Every field optional; ``None`` means "leave unchanged"
    because these columns are all non-nullable in the database.
    """

    name: str | None = Field(default=None, min_length=2, max_length=255)
    default_locale: Language | None = None
    branding: dict[str, object] | None = None
    sso_issuer_url: str | None = Field(default=None, max_length=512)


class TenantQuotaRead(APIModel):
    """Quota snapshot for the settings screen and the pre-flight check."""

    seats_limit: int
    seats_used: int
    storage_quota_bytes: int
    storage_used_bytes: int
    monthly_generation_limit: int
    generations_this_month: int

    @property
    def storage_exhausted(self) -> bool:
        return self.storage_used_bytes >= self.storage_quota_bytes


class TenantRead(TimestampedRead):
    name: str
    slug: str
    plan: TenantPlan
    is_active: bool
    default_locale: Language
    branding: dict[str, object]
    # NB: sso_issuer_url is intentionally absent from the default read model —
    # it is exposed only on the owner-scoped settings endpoint.
