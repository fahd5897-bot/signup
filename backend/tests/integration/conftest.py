"""Fixtures for tests that need a real PostgreSQL.

These do not run against SQLite or a mock. Row-Level Security is a PostgreSQL
feature, and a policy that has only ever been asserted in Python is not a
security control — it is a comment.

Skipped automatically when no database is reachable, so the unit suite stays
runnable without infrastructure. Point ``TEST_POSTGRES_DSN`` at a throwaway
database to enable them.
"""

from __future__ import annotations

import os
import uuid

import pytest

TEST_DSN = os.getenv("TEST_POSTGRES_DSN", "postgresql+asyncpg://postgres@127.0.0.1:5433/rfp")
APP_DSN = TEST_DSN.replace("postgres@", "rfp_app@")

pytestmark = pytest.mark.integration


def _reachable() -> bool:
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(TEST_DSN.replace("postgresql+asyncpg", "postgresql"))
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def _require_postgres():
    """Skip the whole module when no database is reachable.

    An autouse fixture rather than an importable skipif marker: test modules
    then need no cross-import from conftest, which pytest only supports if the
    tests directory is a package.
    """
    if not _reachable():
        pytest.skip("no PostgreSQL at TEST_POSTGRES_DSN")


@pytest.fixture
def app_dsn() -> str:
    """DSN for the unprivileged application role."""
    return APP_DSN


@pytest.fixture
async def superuser_engine():
    """Connects as the owner, which is exempt from nothing — the tables are
    FORCE ROW LEVEL SECURITY — but can still create the seed rows because it
    sets the GUC explicitly."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(TEST_DSN)
    yield engine
    await engine.dispose()


@pytest.fixture
async def two_tenants(superuser_engine):
    """Two tenants, each with one user. Returns (tenant_a_id, tenant_b_id)."""
    from sqlalchemy import text

    a, b = uuid.uuid4(), uuid.uuid4()
    async with superuser_engine.begin() as conn:
        for tenant_id, name in ((a, "acme"), (b, "globex")):
            await conn.execute(
                text(
                    "INSERT INTO tenants (id,name,slug,plan,is_active,default_locale,"
                    "seats_limit,storage_quota_bytes,monthly_generation_limit,branding) "
                    "VALUES (:i,:n,:s,'trial',true,'en',5,1073741824,100,'{}')"
                ),
                {"i": tenant_id, "n": name, "s": f"{name}-{tenant_id.hex[:8]}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id,tenant_id,email,full_name,role,is_active,locale) "
                    "VALUES (:u,:t,:e,:f,'owner',true,'en')"
                ),
                {"u": uuid.uuid4(), "t": tenant_id, "e": f"a@{name}.com", "f": name},
            )
    yield a, b
    async with superuser_engine.begin() as conn:
        await conn.execute(text("DELETE FROM tenants WHERE id = ANY(:ids)"), {"ids": [a, b]})


@pytest.fixture
def two_tenants_sync(superuser_engine_sync):
    """Two tenants, seeded from synchronous code.

    Needed by the Celery task tests: those must run with no ambient event loop,
    because that is the only situation the worker is ever in, and an async
    fixture would leave one turning.
    """
    from sqlalchemy import text

    a, b = uuid.uuid4(), uuid.uuid4()
    with superuser_engine_sync.begin() as conn:
        for tenant_id, name in ((a, "acme"), (b, "globex")):
            conn.execute(
                text(
                    "INSERT INTO tenants (id,name,slug,plan,is_active,default_locale,"
                    "seats_limit,storage_quota_bytes,monthly_generation_limit,branding) "
                    "VALUES (:i,:n,:s,'trial',true,'en',5,1073741824,100,'{}')"
                ),
                {"i": tenant_id, "n": name, "s": f"{name}-{tenant_id.hex[:8]}"},
            )
            conn.execute(
                text(
                    "INSERT INTO users (id,tenant_id,email,full_name,role,is_active,locale) "
                    "VALUES (:u,:t,:e,:f,'owner',true,'en')"
                ),
                {"u": uuid.uuid4(), "t": tenant_id, "e": f"a@{name}.com", "f": name},
            )
    yield a, b
    with superuser_engine_sync.begin() as conn:
        conn.execute(text("DELETE FROM tenants WHERE id = ANY(:ids)"), {"ids": [a, b]})


@pytest.fixture
def superuser_engine_sync():
    """Synchronous owner connection, for fixtures that must not start a loop."""
    from sqlalchemy import create_engine

    engine = create_engine(TEST_DSN.replace("postgresql+asyncpg", "postgresql+psycopg"))
    yield engine
    engine.dispose()
