"""Rate limiting, applied where an unauthenticated caller can reach.

The login endpoint is the one that matters. Argon2id is deliberately expensive
— 64 MB and roughly a tenth of a second per verification — which is what makes
a stolen hash worth little, and also what makes an unthrottled login endpoint a
cheap denial-of-service: a few hundred concurrent attempts saturate memory and
CPU on the API without any of them succeeding. The same limit slows credential
stuffing to a rate where the timing-safe "wrong password or unknown email"
answer actually holds up.

Keyed on client IP, with the storage backed by Redis so the limit is shared
across replicas. A per-process limit multiplies by the number of pods, which is
the same as having a much weaker one and not knowing it.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

#: Unauthenticated credential checks. Generous enough for a person mistyping a
#: password on a shared office IP, far below what a stuffing run needs.
AUTH_LIMIT = "10/minute"

#: Everything else, as a backstop against a runaway client rather than an
#: attacker — an authenticated caller is already accountable.
DEFAULT_LIMIT = "300/minute"


def _client_key(request: Request) -> str:
    """Prefer the proxy-supplied client address.

    Behind a load balancer every request appears to come from the balancer, and
    a limiter keyed on that throttles the entire customer base as one client.
    Only the first entry is used, and only because the proxy is trusted to
    rewrite it — a header from the internet is attacker-controlled.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


def build_limiter(settings: Settings) -> Limiter:
    """Construct the limiter. Called once at import; see ``limiter`` below."""
    return Limiter(
        key_func=_client_key,
        default_limits=[DEFAULT_LIMIT],
        # Shared across replicas. In-memory storage would give each pod its own
        # allowance, quietly multiplying every limit by the replica count.
        storage_uri=str(settings.redis_url),
        # Do not take the API down because the limiter's own backend is down.
        # A brief outage of the counter is a smaller problem than refusing
        # every request while it recovers.
        in_memory_fallback_enabled=True,
        headers_enabled=True,
    )


#: Module-level, because slowapi's per-route limits are decorators and a
#: decorator cannot reach an instance created later per application. Settings
#: are cached, so this is the same configuration the app is built with.
limiter: Limiter = build_limiter(get_settings())


def _rate_limit_exceeded(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """RFC 9457-shaped, matching every other error the API returns.

    slowapi's default body is a bare string, which the frontend cannot branch
    on — and this is one of the few errors it genuinely needs to explain
    rather than retry.
    """
    logger.warning("rate limit hit: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=429,
        content={
            "type": "rate_limited",
            "title": "Too many requests.",
            "detail": f"Rate limit exceeded: {exc.detail}",
        },
        headers={"Retry-After": "60"},
    )


def configure_rate_limiting(app: FastAPI, settings: Settings | None = None) -> None:
    """Attach the shared limiter to one application."""
    settings = settings or get_settings()
    limiter.enabled = settings.rate_limit_enabled
    if not settings.rate_limit_enabled:
        logger.warning("rate limiting is DISABLED; do not run this way in production")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded)
    app.add_middleware(SlowAPIMiddleware)


__all__ = [
    "AUTH_LIMIT",
    "DEFAULT_LIMIT",
    "build_limiter",
    "configure_rate_limiting",
    "limiter",
]
