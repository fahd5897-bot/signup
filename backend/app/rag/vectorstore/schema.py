"""Qdrant payload contract.

Single source of truth for what a point carries. The field *names* live here as
constants rather than as string literals scattered across retrieval code,
because a typo in a filter key does not raise — Qdrant simply matches nothing,
and a tenant filter that matches nothing is a silent isolation failure that
looks exactly like "no results found".
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.enums import ChunkType, DocumentRole, Language

#: Sentinel stored in ``workspace_id`` for tenant-wide knowledge-base chunks.
#:
#: The obvious encoding — omit the key, then filter with ``IsNull`` — does not
#: work: Qdrant's ``is_null`` matches an explicit null, not an *absent* key, so
#: global chunks silently vanish from every workspace-scoped search. That is a
#: particularly nasty failure because retrieval still returns results (the
#: workspace's own documents), so it reads as "poor recall" rather than a bug.
#:
#: An explicit sentinel avoids null semantics entirely, and being a real
#: keyword value it is served by the payload index instead of a scan.
GLOBAL_WORKSPACE: Final = "__tenant_global__"


class PayloadField:
    """Canonical payload key names."""

    TENANT_ID: Final = "tenant_id"
    WORKSPACE_ID: Final = "workspace_id"
    DOCUMENT_ID: Final = "document_id"
    DOCUMENT_NAME: Final = "document_name"
    DOCUMENT_ROLE: Final = "document_role"
    PAGE_NUMBER: Final = "page_number"
    CHUNK_TYPE: Final = "chunk_type"
    CHUNK_INDEX: Final = "chunk_index"
    LANGUAGE: Final = "language"
    SECTION_PATH: Final = "section_path"
    TEXT: Final = "text"
    RAW_TEXT: Final = "raw_text"
    CHAR_START: Final = "char_start"
    CHAR_END: Final = "char_end"
    BBOX: Final = "bbox"
    INDEXED_AT: Final = "indexed_at"


class VectorName:
    """Named vectors on each point.

    Hybrid retrieval needs both: dense vectors capture "we have ISO 27001"
    matching "information-security certification", while sparse vectors are
    what actually find a literal tender reference like ``MOH/2026/IT/0114`` or
    an exact Arabic legal term. Neither alone is sufficient on tender language.
    """

    DENSE: Final = "dense"
    SPARSE: Final = "sparse"


class ChunkPayload(BaseModel):
    """Validated payload written to every Qdrant point.

    Constructed and validated before upsert so malformed payloads fail in the
    worker — where the failure is visible and retryable — rather than surfacing
    later as a citation that cannot resolve.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    # ------------------------------------------------------- isolation keys
    #: The isolation discriminator. Indexed with ``is_tenant=True``, which also
    #: makes Qdrant co-locate each tenant's vectors on disk.
    tenant_id: str
    #: NULL/absent means a tenant-wide knowledge-base chunk, visible to every
    #: workspace. Set means the chunk is confined to one pursuit.
    workspace_id: str | None = None

    # ------------------------------------------------------- source identity
    document_id: str
    document_name: str
    document_role: DocumentRole
    #: 1-indexed to match what a reader sees in a PDF viewer. Absent for
    #: formats with no pagination (xlsx, csv, html).
    page_number: int | None = Field(default=None, ge=1)
    chunk_type: ChunkType
    #: Ordinal within the document; gives deterministic ordering and lets the
    #: reviewer UI fetch neighbouring chunks for context.
    chunk_index: int = Field(ge=0)

    # -------------------------------------------------------------- content
    #: Retrieval-normalised text (Arabic dediacritised, alef/ya unified).
    text: str
    #: Original text exactly as extracted. Citations must quote *this*, or the
    #: highlight will not align with the rendered source document.
    raw_text: str
    language: Language
    section_path: str | None = None

    # ------------------------------------------------- citation anchors
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    #: [x0, y0, x1, y1] in PDF user space, for drawing the highlight rectangle.
    bbox: list[float] | None = None
    indexed_at: str | None = None

    @property
    def is_global(self) -> bool:
        """True when this chunk is visible to every workspace in the tenant."""
        return self.workspace_id is None

    def to_qdrant(self) -> dict[str, Any]:
        """Serialise for upsert.

        ``None`` values are dropped so absent beats null — except
        ``workspace_id``, which is always written, using :data:`GLOBAL_WORKSPACE`
        when the chunk is tenant-wide. See that constant for why.
        """
        data = {k: v for k, v in self.model_dump(mode="json").items() if v is not None}
        data[PayloadField.WORKSPACE_ID] = self.workspace_id or GLOBAL_WORKSPACE
        return data
