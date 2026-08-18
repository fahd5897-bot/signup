"""Tenant isolation, asserted against a live PostgreSQL.

Every test here answers "can tenant A reach tenant B's data?" for one verb.
Together they are the evidence behind the multi-tenancy claim in
ARCHITECTURE.md — without them the policies are untested configuration.
"""

from __future__ import annotations

import uuid

import pytest
from app.db.session import TenantScopeError, privileged_session, tenant_session
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _app_role_dsn(monkeypatch, app_dsn):
    """Connect as the unprivileged application role.

    Connecting as the owner would be a meaningless test: PostgreSQL exempts a
    table's owner from its own policies unless FORCE is set, so a passing suite
    would prove nothing about how the app actually connects.
    """
    monkeypatch.setenv("POSTGRES_DSN", app_dsn)

    # Clearing the settings cache is what actually makes this fixture work.
    # get_settings is lru_cached, so if any earlier test in the session already
    # resolved it, the engine is built from THAT DSN — the superuser one — and
    # a superuser bypasses RLS entirely regardless of FORCE ROW LEVEL SECURITY.
    # The suite would then pass in isolation and fail (or worse, silently prove
    # nothing) when run together.
    import app.db.session as session_module
    from app.core.config import get_settings

    def _reset() -> None:
        get_settings.cache_clear()
        session_module._engine = None
        session_module._session_factory = None

    _reset()
    yield
    _reset()


async def _emails(tenant_id) -> list[str]:
    async with tenant_session(tenant_id) as session:
        return list((await session.execute(text("SELECT email FROM users"))).scalars())


async def test_select_is_scoped_to_the_active_tenant(two_tenants):
    a, b = two_tenants
    assert await _emails(a) == ["a@acme.com"]
    assert await _emails(b) == ["a@globex.com"]


async def test_update_cannot_reach_another_tenant(two_tenants):
    a, b = two_tenants
    async with tenant_session(a) as session:
        result = await session.execute(
            text("UPDATE users SET full_name='hijacked' WHERE email='a@globex.com'")
        )
        assert result.rowcount == 0

    async with tenant_session(b) as session:
        name = (await session.execute(text("SELECT full_name FROM users"))).scalar()
    assert name == "globex"


async def test_delete_cannot_reach_another_tenant(two_tenants):
    a, _ = two_tenants
    async with tenant_session(a) as session:
        result = await session.execute(text("DELETE FROM users WHERE email='a@globex.com'"))
        assert result.rowcount == 0


async def test_insert_stamped_with_another_tenant_is_refused(two_tenants):
    """WITH CHECK, not just USING.

    Without WITH CHECK a tenant could write a row owned by someone else and
    then never see it again — data poisoning that leaves no trace in its own
    view.
    """
    a, b = two_tenants
    with pytest.raises(Exception, match="(?i)policy|row-level"):
        async with tenant_session(a) as session:
            await session.execute(
                text(
                    "INSERT INTO users (id,tenant_id,email,full_name,role,is_active,locale) "
                    "VALUES (:u,:t,'planted@x.com','planted','owner',true,'en')"
                ),
                {"u": uuid.uuid4(), "t": b},
            )


async def test_session_without_tenant_sees_nothing(two_tenants):
    """Unscoped must mean "no rows", never "all rows"."""
    async with privileged_session() as session:
        rows = list((await session.execute(text("SELECT email FROM users"))).scalars())
    assert rows == []


async def test_tenant_does_not_leak_across_pooled_connections(two_tenants):
    """Regression: the GUC is transaction-local.

    A session-local setting would survive connection checkin and the next
    request to borrow that connection would inherit the previous tenant.
    """
    a, _ = two_tenants
    for _ in range(3):
        async with tenant_session(a) as session:
            assert (
                await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
            ).scalar() == str(a)
        async with privileged_session() as session:
            carried = (
                await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
            ).scalar()
        assert not carried


async def test_released_guc_reverts_to_empty_string_not_null(two_tenants):
    """Regression for the NULLIF in the policies.

    Once a transaction has set and released a custom GUC, the value reverts to
    the empty string rather than NULL, and ''::uuid raises "invalid input
    syntax for type uuid". Before NULLIF, the first request on a connection
    worked and every later one failed hard.
    """
    a, _ = two_tenants
    async with tenant_session(a) as session:
        await session.execute(text("SELECT 1"))

    # Same pooled connection, now carrying the reset value.
    async with privileged_session() as session:
        value = (
            await session.execute(text("SELECT current_setting('app.tenant_id', true)"))
        ).scalar()
        assert value in ("", None)
        # The query below is what used to raise.
        rows = list((await session.execute(text("SELECT email FROM users"))).scalars())
    assert rows == []


async def test_application_role_cannot_bypass_rls(superuser_engine):
    """BYPASSRLS or superuser on the app role silently voids every policy."""
    async with superuser_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname='rfp_app'")
            )
        ).first()
    assert row is not None, "the rfp_app role was never created"
    assert row.rolbypassrls is False
    assert row.rolsuper is False


async def test_opening_a_session_without_a_tenant_id_is_refused():
    with pytest.raises(TenantScopeError):
        async with tenant_session(""):
            pass


async def test_the_suite_is_not_secretly_running_as_the_owner(app_dsn):
    """Everything in this file is meaningless if the connection is privileged.

    A superuser is exempt from nothing here — the tables are FORCE ROW LEVEL
    SECURITY — but it can still create, drop, and alter policies, and a DSN
    that quietly resolves back to the owner has happened once already: the
    app-role URL used to be derived by string replacement that did not match a
    password-bearing URL, so CI would have run the entire isolation suite as
    postgres and passed.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(app_dsn)
    try:
        async with engine.connect() as connection:
            role, is_super = (
                await connection.execute(
                    text("SELECT current_user, usesuper FROM pg_user WHERE usename = current_user")
                )
            ).one()
    finally:
        await engine.dispose()

    assert is_super is False, f"integration tests are connected as a superuser ({role})"
