"""Liveness and readiness.

Two endpoints, because they answer different questions and conflating them
takes a service down for the wrong reason:

* ``/health`` — is this process alive? No dependencies are touched. An orchestrator
  restarting the container because PostgreSQL blinked turns a brief database
  outage into a full outage with a cold start on the far side.
* ``/ready`` — should this process receive traffic? Checks the dependencies a
  request actually needs, so a pod with a broken vector store is taken out of
  the load balancer instead of serving errors.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine
from app.schemas.base import APIModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class Health(APIModel):
    status: str
    environment: str


class Readiness(APIModel):
    ready: bool
    #: Per-dependency verdict, so an on-call engineer sees which one failed
    #: without opening logs.
    checks: dict[str, str]


@router.get("/health", response_model=Health, summary="Liveness")
async def health() -> Health:
    settings = get_settings()
    return Health(status="ok", environment=settings.environment)


@router.get("/ready", response_model=Readiness, summary="Readiness")
async def ready(request: Request, response: Response) -> Readiness:
    """Report on each dependency rather than failing at the first one.

    A single boolean hides which dependency is down, which is the first thing
    anyone needs to know at three in the morning.
    """
    checks: dict[str, str] = {}

    try:
        engine = get_engine()
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 - the verdict is the point, not the type
        logger.warning("readiness: postgres unavailable: %s", exc)
        checks["postgres"] = "unavailable"

    qdrant = getattr(request.app.state, "qdrant", None)
    if qdrant is None:
        checks["qdrant"] = "not_initialised"
    else:
        try:
            await qdrant.get_collections()
            checks["qdrant"] = "ok"
        except Exception as exc:  # noqa: BLE001
            logger.warning("readiness: qdrant unavailable: %s", exc)
            checks["qdrant"] = "unavailable"

    is_ready = all(value == "ok" for value in checks.values())
    if not is_ready:
        # 503 rather than 200-with-a-flag: load balancers read the status code,
        # not the body.
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(ready=is_ready, checks=checks)
