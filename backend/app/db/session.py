"""Async session factory with per-transaction tenant scoping.

Every database session opened through :func:`tenant_session` sets
``app.tenant_id`` before any query runs, which is the value every Row-Level
Security policy reads. Nothing else in the codebase may open a session against
these tables — that rule is what makes tenant isolation reviewable in one file
rather than at every call site, exactly as ``rag/vectorstore/filters.py`` does
for the vector store.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


class TenantScopeError(RuntimeError):
    """Raised when a session would be opened without a tenant."""


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Process-wide engine. Created once; pooled for the app's lifetime."""
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        _engine = create_async_engine(
            str(settings.postgres_dsn),
            pool_pre_ping=True,
            # Connections are reused across requests, which is precisely why
            # the tenant GUC below is set with is_local=true — a session-scoped
            # setting would survive checkin and leak into the next request.
            pool_size=10,
            max_overflow=20,
            echo=False,
        )
    return _engine


def get_session_factory(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(settings), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


@asynccontextmanager
async def tenant_session(
    tenant_id: uuid.UUID | str,
    settings: Settings | None = None,
) -> AsyncIterator[AsyncSession]:
    """Open a session scoped to one tenant for the life of its transaction.

    ``set_config(..., true)`` makes the setting **transaction-local**: it is
    discarded at COMMIT or ROLLBACK. With a connection pool that distinction is
    the whole ballgame — a session-local setting would persist on the pooled
    connection and the next request to borrow it would inherit the previous
    request's tenant.

    Raises:
        TenantScopeError: if ``tenant_id`` is empty.
    """
    if not tenant_id:
        raise TenantScopeError("tenant_id is required to open a database session")

    factory = get_session_factory(settings)
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            yield session


@asynccontextmanager
async def privileged_session(
    settings: Settings | None = None,
) -> AsyncIterator[AsyncSession]:
    """Session with no tenant set — for signup, login lookup, and migrations.

    Deliberately awkward to reach for. With ``app.tenant_id`` unset,
    ``current_setting('app.tenant_id', true)`` returns NULL and every policy
    comparison evaluates to NULL, so **no rows are visible**. That is the safe
    default: an unscoped session sees nothing rather than everything.

    Cross-tenant work (admin tooling, the retention job) therefore needs a role
    with BYPASSRLS, which the application role deliberately does not have.
    """
    factory = get_session_factory(settings)
    async with factory() as session:
        async with session.begin():
            yield session


async def dispose_engine() -> None:
    """Close the pool. Called from the app's lifespan shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
