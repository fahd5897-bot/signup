"""CORS configuration.

These are integration-shaped on purpose. CORS failures are invisible in unit
tests and expensive in the browser: a response missing one header arrives as an
opaque network error, and the frontend cannot tell a backend bug from the API
being down from a genuine misconfiguration.
"""

from __future__ import annotations

import pytest
from app.api.middleware.error_handler import (
    ExceptionToResponseMiddleware,
    register_exception_handlers,
)
from app.api.v1.routers import documents, generation
from app.main import _configure_cors
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

ALLOWED = "http://localhost:3000"
DENIED = "https://evil.example.com"


@pytest.fixture
def client(settings) -> TestClient:
    app = FastAPI()
    # Same ordering as create_app(): catcher inside, CORS outside.
    app.add_middleware(ExceptionToResponseMiddleware)
    _configure_cors(app, settings)
    register_exception_handlers(app)
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(generation.router, prefix="/api/v1")

    @app.get("/api/v1/_boom")
    async def _boom() -> None:
        raise RuntimeError("unexpected")

    @app.get("/api/v1/_export_stub")
    async def _export_stub() -> Response:
        return Response(
            content=b"x",
            media_type="application/octet-stream",
            headers={"Content-Disposition": 'attachment; filename="moh-2026.xlsx"'},
        )

    return TestClient(app)


def test_preflight_allows_multipart_upload(client: TestClient) -> None:
    """The upload endpoint is a multipart POST, so it always preflights."""
    response = client.options(
        "/api/v1/upload-document",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]
    # Preflight caching; without it every generate-answer costs two requests.
    assert response.headers["access-control-max-age"] == "600"


def test_disallowed_origin_is_not_echoed(client: TestClient) -> None:
    response = client.options(
        "/api/v1/generate-answer",
        headers={"Origin": DENIED, "Access-Control-Request-Method": "POST"},
    )
    assert response.headers.get("access-control-allow-origin") not in (DENIED, "*")


def test_wildcard_origin_is_never_configured(settings) -> None:
    """A wildcard origin with credentials is rejected by every browser.

    Starlette configures it without complaint, so the guard has to live here.
    """
    assert "*" not in settings.cors_origins


@pytest.mark.parametrize(
    ("path", "method", "expected_status"),
    [
        ("/api/v1/generate-answer", "post", 401),  # handled AppError
        ("/api/v1/nope", "get", 404),  # router miss
        ("/api/v1/_boom", "get", 500),  # unhandled exception
    ],
)
def test_error_responses_carry_cors_headers(
    client: TestClient, path: str, method: str, expected_status: int
) -> None:
    """Every error class must be readable by the browser.

    The 500 case is the one that regressed: Starlette's own catch-all runs in
    ServerErrorMiddleware, *outside* CORSMiddleware, so an unhandled exception
    produced a 500 with no CORS headers. ExceptionToResponseMiddleware exists to
    catch it inside the CORS layer instead.
    """
    kwargs = {"json": {"requirement_ref": "1", "requirement_text": "Q"}} if method == "post" else {}
    response = getattr(client, method)(path, headers={"Origin": ALLOWED}, **kwargs)

    assert response.status_code == expected_status
    assert response.headers.get("access-control-allow-origin") == ALLOWED


def test_unhandled_error_body_leaks_nothing(client: TestClient) -> None:
    """Exception text can carry filenames, paths, and query fragments."""
    response = client.get("/api/v1/_boom", headers={"Origin": ALLOWED})
    body = response.json()
    assert body == {"type": "internal_error", "title": "An unexpected error occurred."}
    assert "unexpected" not in str(body).replace("An unexpected error occurred.", "")


def test_cors_origins_parses_comma_separated_env(monkeypatch) -> None:
    """Regression: pydantic-settings JSON-decodes list fields from env before
    any validator runs, so `CORS_ORIGINS=a,b` raised a SettingsError at import.
    """
    from app.core.config import Settings

    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com, https://b.example.com")
    monkeypatch.setenv("SUPPORTED_LOCALES", "en,ar")
    settings = Settings()
    assert settings.cors_origins == ["https://a.example.com", "https://b.example.com"]
    assert settings.supported_locales == ["en", "ar"]


def test_the_download_filename_survives_a_cross_origin_export(client: TestClient) -> None:
    """`Content-Disposition` must be named in `expose_headers`.

    The failure is silent and only reproduces in a browser: the header arrives,
    the browser withholds it from JavaScript, and the client falls back to a
    generic name as though the server never sent one. A submission document
    reaching a procurement authority as "tender-response.xlsx" instead of the
    named artefact is not a cosmetic problem.
    """
    response = client.get("/api/v1/_export_stub", headers={"Origin": ALLOWED})
    exposed = {
        h.strip().lower()
        for h in response.headers.get("access-control-expose-headers", "").split(",")
    }
    assert "content-disposition" in exposed
    assert "x-request-id" in exposed
