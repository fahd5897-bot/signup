"""Tenant-scoped filter construction — the single isolation choke point.

Every Qdrant query in the codebase must obtain its filter from this module.
Nothing else may construct a ``models.Filter`` for the chunk collection. The
value of that rule is that tenant isolation becomes reviewable: it is one small
file, not a property you have to re-verify at every call site.

Enforced in CI by a lint rule that fails on ``models.Filter(`` outside this
module (see ``scripts/check_filter_usage.py``).
"""

from __future__ import annotations

import uuid

from qdrant_client import models

from app.db.models.enums import ChunkType, DocumentRole, Language
from app.rag.vectorstore.schema import GLOBAL_WORKSPACE, PayloadField


class TenantScopeError(RuntimeError):
    """Raised when a filter would be built without a tenant scope."""


def build_search_filter(
    *,
    tenant_id: uuid.UUID | str,
    workspace_id: uuid.UUID | str | None = None,
    document_ids: list[uuid.UUID | str] | None = None,
    chunk_types: list[ChunkType] | None = None,
    languages: list[Language] | None = None,
    page_range: tuple[int, int] | None = None,
    include_tender_documents: bool = False,
) -> models.Filter:
    """Build the filter for a retrieval query.

    ``tenant_id`` is keyword-only and has no default: omitting it is a
    ``TypeError`` at the call site, not a silently unscoped search.

    Args:
        tenant_id: Owning tenant. Always applied.
        workspace_id: When given, widens to "this workspace's documents **or**
            the tenant-wide knowledge base" — not narrows to the workspace
            alone. Global knowledge is the main source of answers; a workspace
            merely adds its own confidential material on top.
        document_ids: Restrict to specific sources (reviewer's "answer this
            from the technical specification only").
        chunk_types: Bias retrieval by shape — pricing questions want TABLE.
        languages: Restrict by chunk language.
        page_range: Inclusive ``(first, last)`` page window.
        include_tender_documents: Off by default, and deliberately awkward to
            turn on. Tender documents are the *question*; letting them serve as
            evidence produces answers that cite the customer's own requirement
            back at them. Only the requirement-extraction path sets this True.

    Raises:
        TenantScopeError: If ``tenant_id`` is empty.
    """
    if not tenant_id:
        raise TenantScopeError("tenant_id is required for every vector search")

    must: list[models.Condition] = [
        models.FieldCondition(
            key=PayloadField.TENANT_ID,
            match=models.MatchValue(value=str(tenant_id)),
        )
    ]
    must_not: list[models.Condition] = []

    if workspace_id is not None:
        # "belongs to this workspace OR is tenant-wide knowledge".
        # A single MatchAny over two keyword values — not a should-clause with
        # IsNull, which would not match global chunks at all (see
        # GLOBAL_WORKSPACE in schema.py).
        must.append(
            models.FieldCondition(
                key=PayloadField.WORKSPACE_ID,
                match=models.MatchAny(any=[str(workspace_id), GLOBAL_WORKSPACE]),
            )
        )

    if not include_tender_documents:
        must_not.append(
            models.FieldCondition(
                key=PayloadField.DOCUMENT_ROLE,
                match=models.MatchValue(value=DocumentRole.TENDER.value),
            )
        )

    if document_ids:
        must.append(
            models.FieldCondition(
                key=PayloadField.DOCUMENT_ID,
                match=models.MatchAny(any=[str(d) for d in document_ids]),
            )
        )

    if chunk_types:
        must.append(
            models.FieldCondition(
                key=PayloadField.CHUNK_TYPE,
                match=models.MatchAny(any=[c.value for c in chunk_types]),
            )
        )

    if languages:
        must.append(
            models.FieldCondition(
                key=PayloadField.LANGUAGE,
                match=models.MatchAny(any=[lang.value for lang in languages]),
            )
        )

    if page_range is not None:
        first, last = page_range
        must.append(
            models.FieldCondition(
                key=PayloadField.PAGE_NUMBER,
                range=models.Range(gte=first, lte=last),
            )
        )

    return models.Filter(must=must, must_not=must_not or None)


def build_tenant_only_filter(tenant_id: uuid.UUID | str) -> models.Filter:
    """Bare tenant filter, for maintenance operations (counts, exports, purge)."""
    if not tenant_id:
        raise TenantScopeError("tenant_id is required")
    return models.Filter(
        must=[
            models.FieldCondition(
                key=PayloadField.TENANT_ID,
                match=models.MatchValue(value=str(tenant_id)),
            )
        ]
    )


def build_document_filter(
    tenant_id: uuid.UUID | str, document_id: uuid.UUID | str
) -> models.Filter:
    """One document's points, within one tenant.

    The tenant condition is kept even though ``document_id`` is already unique.
    Defence in depth: this filter drives deletion, and a malformed or
    attacker-supplied document ID must not be able to reach another tenant's
    points.
    """
    if not tenant_id:
        raise TenantScopeError("tenant_id is required")
    if not document_id:
        raise TenantScopeError("document_id is required")
    return models.Filter(
        must=[
            models.FieldCondition(
                key=PayloadField.TENANT_ID,
                match=models.MatchValue(value=str(tenant_id)),
            ),
            models.FieldCondition(
                key=PayloadField.DOCUMENT_ID,
                match=models.MatchValue(value=str(document_id)),
            ),
        ]
    )
