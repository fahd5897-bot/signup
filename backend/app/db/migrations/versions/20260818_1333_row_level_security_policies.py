"""row level security policies

Revision ID: 8a739c298772
Revises: a78e778fe2d2
Create Date: 2026-08-18 13:33:06.192052
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8a739c298772"
down_revision: str | None = "a78e778fe2d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables carrying a tenant_id. `tenants` itself is deliberately absent: it is
# the table the discriminator points at, so it has no tenant_id of its own.
# ruff: noqa: S608 - table names below are module-level literals, never request
# data; PostgreSQL has no bind-parameter syntax for identifiers in DDL.
TENANT_TABLES = ("users", "workspaces", "documents", "generated_proposals")

#: Role the application connects as. Critically NOT the owner of these tables —
#: PostgreSQL exempts a table's owner from its own RLS policies unless
#: FORCE ROW LEVEL SECURITY is set, so an app running as owner would silently
#: bypass every policy below while appearing to have them enabled.
APP_ROLE = "rfp_app"


def upgrade() -> None:
    # The GUC every policy reads. set_config(..., true) scopes it to the
    # transaction, so a pooled connection cannot leak one request's tenant
    # into the next.
    op.execute(
        sa.text(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN;
            END IF;
        END
        $$;
    """)
    )

    op.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
    op.execute(
        sa.text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
        )
    )
    op.execute(
        sa.text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
        )
    )

    for table in TENANT_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        # Belt and braces: FORCE makes the policy apply even to the table owner,
        # so a future migration or an admin session cannot quietly read across
        # tenants.
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

        # One policy covering all four verbs. USING gates what a statement can
        # see or modify; WITH CHECK gates what it can write — without the
        # latter, a tenant could INSERT a row stamped with someone else's
        # tenant_id and then never be able to see it again.
        #
        # NULLIF is required for correctness, not defensiveness. An unset
        # custom GUC returns NULL, but once a transaction has set and released
        # it the value reverts to the EMPTY STRING, and ''::uuid raises
        # "invalid input syntax for type uuid". On a pooled connection that
        # means the first request succeeds and every later request borrowing
        # that connection fails hard — an intermittent 500 that looks like
        # anything but a policy bug. NULLIF maps both states to NULL, so the
        # comparison yields NULL and the row is denied: unset means "see
        # nothing", never "crash" and never "see everything".
        op.execute(
            sa.text(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
                USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """)
        )

    # The tenants table is readable only as the row matching the active tenant.
    op.execute(sa.text("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE tenants FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text("""
        CREATE POLICY tenants_self_isolation ON tenants
            USING (id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (id = nullif(current_setting('app.tenant_id', true), '')::uuid)
    """)
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenants_self_isolation ON tenants"))
    op.execute(sa.text("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY"))
    for table in TENANT_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}"))
