"""stale upload lookup

Revision ID: e7b3a91c4d02
Revises: c1f4b2d7e803
Create Date: 2026-08-18 20:15:02.771904
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b3a91c4d02"
down_revision: str | None = "c1f4b2d7e803"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The second, and only other, narrow hole in RLS.

    An upload writes the object, writes the row, and hands the job to Celery.
    If the broker is unreachable at that moment the first two have already
    committed, so failing the request would tell the customer their upload was
    lost when it was not. The document stays at UPLOADED — and without
    something to pick it up again, it stays there forever: the UI polls a
    status that will never change, and the file is silently never indexed.

    The sweeper that re-queues those documents is inherently cross-tenant, and
    cannot run inside a tenant-scoped session because it does not yet know
    which tenants have stuck uploads. Same problem as login, same answer as
    ``auth_lookup_user``: a SECURITY DEFINER function returning exactly the
    columns needed to re-queue, and nothing else.

    Note what it does NOT return: no document text, no proposals, no user data.
    It yields the identifiers of files the system already knows it owns.
    """
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION stale_uploaded_documents(
                p_older_than_seconds integer,
                p_limit integer DEFAULT 200
            )
            RETURNS TABLE (
                document_id uuid,
                tenant_id uuid,
                storage_key text,
                filename text,
                mime_type text,
                role text,
                workspace_id uuid
            )
            LANGUAGE sql
            SECURITY DEFINER
            -- Pinned, or a caller could create a shadow `documents` table in a
            -- schema earlier on their own path and have this definer-rights
            -- function read it instead.
            SET search_path = public, pg_temp
            AS $$
                SELECT d.id, d.tenant_id, d.storage_key, d.filename,
                       d.mime_type, d.role::text, d.workspace_id
                FROM documents d
                WHERE d.status = 'uploaded'
                  AND d.deleted_at IS NULL
                  -- `updated_at` rather than `created_at`: a document the
                  -- sweeper has already re-queued has been touched, so this
                  -- backs off instead of re-queueing it every minute.
                  AND d.updated_at < now() - make_interval(secs => p_older_than_seconds)
                ORDER BY d.updated_at
                LIMIT p_limit
            $$;
            """
        )
    )
    op.execute(
        sa.text("REVOKE ALL ON FUNCTION stale_uploaded_documents(integer, integer) FROM PUBLIC")
    )
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rfp_app') THEN
                    GRANT EXECUTE ON FUNCTION stale_uploaded_documents(integer, integer) TO rfp_app;
                END IF;
            END $$;
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP FUNCTION IF EXISTS stale_uploaded_documents(integer, integer)"))
