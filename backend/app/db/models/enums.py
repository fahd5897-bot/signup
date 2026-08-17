"""Enumerations shared by the ORM models and the Pydantic schemas.

Defined once and imported by both layers so the API contract cannot drift from
the database constraint. All are persisted as native PostgreSQL enum types;
adding a member therefore requires an Alembic migration (``ALTER TYPE ... ADD
VALUE``), which is the intended friction — a silently added status would flow
into the review workbench unhandled.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """Tenant-scoped RBAC. A user holds exactly one role per tenant."""

    OWNER = "owner"  # billing, member management, tenant deletion
    BID_MANAGER = "bid_manager"  # create workspaces, generate, approve, export
    SME = "sme"  # subject-matter expert: answer assigned items only
    VIEWER = "viewer"  # read-only


class TenantPlan(StrEnum):
    TRIAL = "trial"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class WorkspaceStatus(StrEnum):
    """Lifecycle of a single RFP/RFQ/tender pursuit."""

    DRAFT = "draft"  # created, documents still being uploaded
    ANALYZING = "analyzing"  # requirement extraction running
    GENERATING = "generating"  # answers being drafted
    IN_REVIEW = "in_review"  # human-in-the-loop review underway
    APPROVED = "approved"  # every mandatory requirement approved
    SUBMITTED = "submitted"  # exported and sent to the issuing authority
    ARCHIVED = "archived"
    LOST = "lost"  # outcome tracking feeds win/loss analytics


class DocumentRole(StrEnum):
    """What a document is *for*, which decides how it is indexed.

    ``TENDER`` documents are the RFP being answered — parsed for requirements
    and never used as an answer source. ``KNOWLEDGE_BASE`` documents are the
    reusable corpus that answers are grounded in. Conflating the two lets a
    tender's own wording be cited as evidence for the response, which is the
    most common way a "grounded" system produces circular nonsense.
    """

    TENDER = "tender"
    KNOWLEDGE_BASE = "knowledge_base"
    PAST_PROPOSAL = "past_proposal"
    ATTACHMENT = "attachment"


class DocumentStatus(StrEnum):
    PENDING = "pending"  # row created, bytes not yet uploaded
    UPLOADED = "uploaded"  # in object storage, queued for parsing
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    READY = "ready"  # retrievable
    FAILED = "failed"  # see failure_reason; surfaced in the UI
    QUARANTINED = "quarantined"  # failed AV scan or blocked content type


class ParseStrategy(StrEnum):
    """Which Unstructured.io path handled the file.

    Persisted because it is the first thing to check when Arabic extraction
    quality is poor: ``FAST`` on a scanned PDF silently yields empty text.
    """

    FAST = "fast"  # born-digital text layer
    HI_RES = "hi_res"  # layout model + table structure inference
    OCR_ONLY = "ocr_only"  # rasterised, tesseract ara+eng
    NATIVE_TABULAR = "native_tabular"  # xlsx/csv read directly


class ChunkType(StrEnum):
    """Structural kind of an indexed chunk.

    Mirrored into the Qdrant payload so retrieval can bias by shape — pricing
    questions should prefer ``TABLE`` chunks, capability questions ``NARRATIVE``
    — and so the UI knows whether to render a citation as prose or as a grid.
    """

    TITLE = "title"
    NARRATIVE = "narrative"
    TABLE = "table"
    LIST = "list"
    FORM_FIELD = "form_field"
    HEADER_FOOTER = "header_footer"
    IMAGE_CAPTION = "image_caption"


class Language(StrEnum):
    AR = "ar"
    EN = "en"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ProposalStatus(StrEnum):
    """Human-in-the-loop state machine for one generated answer.

    Legal transitions are enforced in the service layer:

        DRAFT ──────────────► PENDING_REVIEW ──► APPROVED ──► EXPORTED
          │                        │  ▲              │
          │                        ▼  │              ▼
          └──► ABSTAINED           REJECTED     (edit) DRAFT

    ``APPROVED`` is reachable only through an explicit human action; nothing in
    the generation pipeline may set it.
    """

    DRAFT = "draft"  # generated, not yet submitted for review
    ABSTAINED = "abstained"  # retrieval gate refused to generate
    PENDING_REVIEW = "pending_review"
    NEEDS_SME = "needs_sme"  # escalated to a subject-matter expert
    REJECTED = "rejected"
    APPROVED = "approved"
    EXPORTED = "exported"


class GroundingVerdict(StrEnum):
    """Result of the post-generation claim/evidence verification pass."""

    VERIFIED = "verified"  # coverage >= threshold, all citations resolve
    PARTIAL = "partial"  # some sentences unsupported; those are flagged
    UNVERIFIED = "unverified"  # below coverage threshold — human edit required
    NOT_APPLICABLE = "not_applicable"  # abstained, so nothing to verify
