"""Workspace (RFP pursuit) schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import Field, computed_field, model_validator

from app.db.models.enums import Language, WorkspaceStatus
from app.schemas.base import APIModel, StrictModel, TimestampedRead


class GroundingConfig(StrictModel):
    """Per-workspace grounding thresholds.

    Snapshotted onto the workspace at creation. A tenant tightening its policy
    next quarter must not retroactively change the confidence figures a
    reviewer already signed off on, so this is stored per pursuit rather than
    read live from tenant settings.
    """

    min_retrieval_score: float = Field(default=0.35, ge=0.0, le=1.0)
    min_citation_coverage: float = Field(default=0.90, ge=0.0, le=1.0)
    abstain_on_ungrounded: bool = True
    require_human_approval: bool = True

    @model_validator(mode="after")
    def _guard_approval(self) -> GroundingConfig:
        """``require_human_approval`` is not a customer-tunable switch.

        It exists as a field so the platform can flip it for internal
        evaluation runs, but no tenant-facing path may set it False — the
        product's core claim is that a human signs every answer.
        """
        if not self.require_human_approval:
            raise ValueError("human approval cannot be disabled")
        return self


class WorkspaceCreate(StrictModel):
    name: str = Field(min_length=2, max_length=255)
    description: str | None = None
    tender_reference: str | None = Field(default=None, max_length=128)
    issuing_authority: str | None = Field(default=None, max_length=255)
    submission_deadline: date | None = None
    estimated_value: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    response_language: Language = Language.EN
    grounding_config: GroundingConfig = Field(default_factory=GroundingConfig)


class WorkspaceUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = None
    tender_reference: str | None = Field(default=None, max_length=128)
    issuing_authority: str | None = Field(default=None, max_length=255)
    submission_deadline: date | None = None
    estimated_value: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    response_language: Language | None = None
    # `status` is absent by design: transitions go through dedicated endpoints
    # that enforce the state machine, not through a general-purpose PATCH.


class WorkspaceStatusUpdate(StrictModel):
    status: WorkspaceStatus
    note: str | None = Field(default=None, max_length=1000)


class WorkspaceRead(TimestampedRead):
    tenant_id: uuid.UUID
    name: str
    description: str | None
    status: WorkspaceStatus
    tender_reference: str | None
    issuing_authority: str | None
    submission_deadline: date | None
    estimated_value: int | None
    currency: str | None
    response_language: Language
    owner_id: uuid.UUID
    requirements_total: int
    requirements_approved: int
    submitted_at: datetime | None

    @computed_field
    @property
    def completion_ratio(self) -> float:
        if self.requirements_total == 0:
            return 0.0
        return round(self.requirements_approved / self.requirements_total, 4)

    @computed_field
    @property
    def is_exportable(self) -> bool:
        """Mirrors ``Workspace.is_exportable`` so the UI can disable the export
        button without a second round trip.
        """
        return self.requirements_total > 0 and self.requirements_approved == self.requirements_total

    @computed_field
    @property
    def days_until_deadline(self) -> int | None:
        if self.submission_deadline is None:
            return None
        return (self.submission_deadline - date.today()).days


class WorkspaceSummary(APIModel):
    """Slim projection for list views — avoids shipping description bodies to
    a dashboard rendering fifty rows.
    """

    id: uuid.UUID
    name: str
    status: WorkspaceStatus
    tender_reference: str | None
    submission_deadline: date | None
    requirements_total: int
    requirements_approved: int
