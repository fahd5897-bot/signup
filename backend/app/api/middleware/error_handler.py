"""Global exception handling.

Two guarantees:

* every :class:`AppError` becomes its declared status code and stable slug, so
  the frontend branches on ``type`` rather than on message text;
* nothing else ever reaches the client as a stack trace. Unhandled exceptions
  become a generic 500 and a logged traceback with the request ID — tender
  documents are commercially sensitive, and an ORM error string can contain
  filenames and SQL.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.exceptions import AppError

logger = logging.getLogger(__name__)


class ExceptionToResponseMiddleware(BaseHTTPMiddleware):
    """Convert unhandled exceptions to a JSON 500 *inside* the CORS layer.

    Starlette already installs a catch-all in ``ServerErrorMiddleware``, but
    that sits at the very outside of the stack — outside ``CORSMiddleware``. An
    unhandled exception therefore produces a 500 with **no CORS headers**, and
    the browser reports it as an opaque ``net::ERR_FAILED`` / CORS violation
    rather than as a server error.

    That failure mode is expensive out of proportion to its cause: the frontend
    cannot tell a backend bug from the API being down from a genuine CORS
    misconfiguration, and whoever debugs it starts by rewriting CORS config
    that was never wrong.

    Registering this middleware *before* the CORS middleware puts it inside,
    so the response it returns still passes back out through CORS and arrives
    as a readable 500.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except AppError as exc:
            # Raised below the routing layer (e.g. in a dependency), where the
            # registered handler does not see it.
            if exc.status_code >= 500:
                logger.exception("server error on %s: %s", request.url.path, exc.detail)
            return JSONResponse(status_code=exc.status_code, content=exc.to_payload())
        except Exception:
            logger.exception("unhandled error on %s", request.url.path)
            return JSONResponse(
                status_code=500,
                content={
                    "type": "internal_error",
                    "title": "An unexpected error occurred.",
                    # No detail: parser and ORM exception text can carry
                    # filenames, paths, and query fragments.
                },
            )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.exception("server error on %s: %s", request.url.path, exc.detail)
        else:
            logger.info("client error on %s: %s (%s)", request.url.path, exc.detail, exc.slug)
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "type": "validation_error",
                "title": "The request could not be validated.",
                "errors": [
                    {
                        "field": ".".join(str(p) for p in err["loc"][1:]),
                        "message": err["msg"],
                    }
                    for err in exc.errors()
                ],
            },
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "type": "internal_error",
                "title": "An unexpected error occurred.",
                # Deliberately no detail: exception text from the ORM or a
                # parser can leak filenames, paths, and query fragments.
            },
        )
