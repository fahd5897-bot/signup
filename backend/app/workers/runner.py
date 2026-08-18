"""Running async work from a synchronous Celery task.

Celery's prefork pool has no event loop, so the tasks are plain functions while
everything they call — the database session, the vector store, the pipeline —
is async. ``asyncio.run`` is the right answer there and the wrong one anywhere
a loop already exists: it raises rather than doing the work, which in a task
means the outcome is never written and the only trace is a log line.

Two things bite here, and both are silent. ``asyncio.run`` builds a new loop
every call, while the database engine is process-wide and bound to whichever
loop first used it — so a worker that ran two of these would reuse connections
belonging to a dead loop. And calling it from inside an existing loop raises
rather than doing the work, which in a task means the outcome is never written
and the only trace is a log line. One helper, used by every task, so no task
has to remember either.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def run_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Run ``coro`` to completion, with or without a loop already running.

    The database engine is disposed afterwards, and that is not tidiness. The
    engine and its pooled connections are process-wide but bound to whichever
    loop first used them, while ``asyncio.run`` builds a *new* loop every call.
    A worker process that ran two of these — the pipeline, then the status
    write — would reuse connections belonging to a loop that no longer exists
    and fail with "attached to a different loop": the ingestion succeeds, the
    vectors land in Qdrant, and the row never leaves UPLOADED.

    A fresh pool per task is the right trade here. Ingestion runs for minutes;
    a connection handshake is noise beside it, and the alternative is a class
    of bug that only appears on the second task a worker handles.
    """

    async def _with_disposal() -> T:
        from app.db.session import dispose_engine

        try:
            return await coro
        finally:
            await dispose_engine()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # The normal case: a prefork worker with no loop of its own.
        return asyncio.run(_with_disposal())

    # Deliberately refused rather than shunted onto a worker thread. The engine
    # is process-wide, so a second loop would borrow connections belonging to
    # the first and corrupt the asyncpg protocol state — an error far stranger
    # to debug than this one. Callers already inside a loop should await the
    # service layer directly; the task functions exist for Celery, which is
    # synchronous.
    raise RuntimeError(
        "run_sync() was called from a running event loop. Await the service "
        "layer directly instead of going through a Celery task body."
    )


__all__ = ["run_sync"]
