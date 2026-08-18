"""auth lookup function

Revision ID: 9a5a496c990f
Revises: 8a739c298772
Create Date: 2026-08-18 14:05:45.247634
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a5a496c990f"
down_revision: str | None = "8a739c298772"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """A narrow, auditable hole in RLS for authentication only.

    Login is inherently cross-tenant: the tenant is not known until the user
    has been identified, so the lookup cannot run inside a tenant-scoped
    session — and an unscoped session sees no rows at all. The usual fixes are
    both bad: granting the application BYPASSRLS voids every policy in the
    system, and a second "auth" role with broad read access is the same hole
    with extra steps.

    A SECURITY DEFINER function is the narrow option. It runs as its owner and
    so bypasses RLS, but it is the ONLY thing that does, it returns exactly the
    six columns authentication needs, and it is greppable — one function to
    review rather than a role whose reach you have to reason about.

    Note what it does NOT return: no answer text, no documents, no proposals.
    Compromising it yields a password hash (Argon2id) and nothing else.
    """
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION auth_lookup_user(p_email text)
            RETURNS TABLE (
                user_id uuid,
                tenant_id uuid,
                email text,
                role text,
                hashed_password text,
                user_active boolean,
                tenant_active boolean,
                tenant_slug text
            )
            LANGUAGE sql
            SECURITY DEFINER
            -- Pin the search_path: without it a caller could create a shadow
            -- `users` table in a schema earlier on their own path and have this
            -- definer-rights function read it instead.
            SET search_path = public, pg_temp
            AS $$
                SELECT u.id, u.tenant_id, u.email, u.role::text,
                       u.hashed_password, u.is_active, t.is_active, t.slug
                FROM users u
                JOIN tenants t ON t.id = u.tenant_id
                WHERE lower(u.email) = lower(p_email)
                  AND u.deleted_at IS NULL
                  AND t.deleted_at IS NULL
            $$;
            """
        )
    )
    op.execute(sa.text("REVOKE ALL ON FUNCTION auth_lookup_user(text) FROM PUBLIC"))
    op.execute(sa.text("GRANT EXECUTE ON FUNCTION auth_lookup_user(text) TO rfp_app"))

    # Registration writes the very first rows of a tenant that does not exist
    # yet. The service sets app.tenant_id to the new tenant's id before
    # inserting, so both the tenants and users policies are satisfied by the
    # normal WITH CHECK path — no exception needed, and no window where an
    # unscoped session can write.


def downgrade() -> None:
    op.execute(sa.text("DROP FUNCTION IF EXISTS auth_lookup_user(text)"))
