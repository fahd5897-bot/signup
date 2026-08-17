"""Pydantic API contracts.

Import from this package rather than from the individual modules, so the
public surface stays visible in one place.
"""

from app.schemas.base import APIModel, PageParams, StrictModel, TimestampedRead
from app.schemas.common import ErrorDetail, Page, TaskAccepted
from app.schemas.document import (
    ALLOWED_MIME_TYPES,
    MAX_UPLOAD_BYTES,
    DocumentFilter,
    DocumentRead,
    DocumentStatusRead,
    DocumentUploadRequest,
    DocumentUploadResponse,
)
from app.schemas.proposal import (
    BatchGenerationRequest,
    Citation,
    ComplianceMatrixRow,
    GenerationRequest,
    GroundingMetrics,
    ProposalEdit,
    ProposalRead,
    ProposalReviewAction,
    ProposalSummary,
)
from app.schemas.tenant import TenantCreate, TenantQuotaRead, TenantRead, TenantUpdate
from app.schemas.user import (
    LoginRequest,
    TokenPair,
    UserCreate,
    UserRead,
    UserRoleUpdate,
    UserUpdate,
)
from app.schemas.workspace import (
    GroundingConfig,
    WorkspaceCreate,
    WorkspaceRead,
    WorkspaceStatusUpdate,
    WorkspaceSummary,
    WorkspaceUpdate,
)

__all__ = [
    "ALLOWED_MIME_TYPES",
    "MAX_UPLOAD_BYTES",
    "APIModel",
    "BatchGenerationRequest",
    "Citation",
    "ComplianceMatrixRow",
    "DocumentFilter",
    "DocumentRead",
    "DocumentStatusRead",
    "DocumentUploadRequest",
    "DocumentUploadResponse",
    "ErrorDetail",
    "GenerationRequest",
    "GroundingConfig",
    "GroundingMetrics",
    "Page",
    "PageParams",
    "ProposalEdit",
    "ProposalRead",
    "ProposalReviewAction",
    "ProposalSummary",
    "StrictModel",
    "TaskAccepted",
    "TenantCreate",
    "TenantQuotaRead",
    "TenantRead",
    "TenantUpdate",
    "TimestampedRead",
    "TokenPair",
    "LoginRequest",
    "UserCreate",
    "UserRead",
    "UserRoleUpdate",
    "UserUpdate",
    "WorkspaceCreate",
    "WorkspaceRead",
    "WorkspaceStatusUpdate",
    "WorkspaceSummary",
    "WorkspaceUpdate",
]
