"""POST /upload-document contract.

Covers the validation gate specifically: everything rejected here is something
that would otherwise reach the parser, and several of them (a truncated PDF, a
mislabelled file) parse "successfully" into partial or wrong content.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from app.api.middleware.error_handler import register_exception_handlers
from app.api.v1.routers import documents as documents_router
from app.db.models.enums import DocumentStatus
from app.security.tokens import issue_access_token
from app.services import storage
from app.services.document_service import RegisteredDocument
from fastapi import FastAPI

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def xlsx_bytes(tmp_path) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["البند", "السعر"])
    path = tmp_path / "b.xlsx"
    workbook.save(path)
    return path.read_bytes()


@pytest.fixture
def client(monkeypatch, settings) -> httpx.AsyncClient:
    """Stub persistence, keep the request path real.

    Everything these tests assert on — type, magic bytes, size, checksum, role
    — is decided before a single byte is stored, so the fake keeps the suite
    free of PostgreSQL and S3. It returns the production dataclass rather than
    a look-alike, so a change to the service contract breaks the test instead
    of quietly passing against a shape the app no longer produces.
    """

    class _FakeService:
        def __init__(self, *args, **kwargs) -> None: ...

        async def register_upload(self, **kwargs) -> RegisteredDocument:
            return RegisteredDocument(
                document_id=uuid.uuid4(),
                status=DocumentStatus.UPLOADED,
                is_duplicate=False,
                task_id="task-1",
            )

    monkeypatch.setattr(documents_router, "DocumentService", _FakeService)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(documents_router.router, prefix="/api/v1")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _token(settings, role: str = "bid_manager") -> dict[str, str]:
    """Mint through the production issuer, not by hand.

    A hand-rolled payload silently omits the `typ` claim the verifier requires,
    so the tests would pass against a token shape the app never issues.
    """
    encoded, _ = issue_access_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        email="user@example.com",
        role=role,
        settings=settings,
    )
    return {"Authorization": f"Bearer {encoded}"}


async def test_accepts_upload_and_returns_poll_url(client, settings, xlsx_bytes) -> None:
    async with client:
        response = await client.post(
            "/api/v1/upload-document",
            headers=_token(settings),
            files={"file": ("كراسة الشروط.xlsx", xlsx_bytes, XLSX_MIME)},
            data={"role": "knowledge_base"},
        )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "uploaded"
    assert body["poll_url"].endswith("/status")


@pytest.mark.parametrize(
    ("header_role", "expected"),
    [("bid_manager", 202), ("owner", 202), ("sme", 202), ("viewer", 403)],
)
async def test_role_gates_upload(client, settings, xlsx_bytes, header_role, expected) -> None:
    async with client:
        response = await client.post(
            "/api/v1/upload-document",
            headers=_token(settings, header_role),
            files={"file": ("a.xlsx", xlsx_bytes, XLSX_MIME)},
            data={"role": "knowledge_base"},
        )
    assert response.status_code == expected


@pytest.mark.parametrize("auth", [{}, {"Authorization": "Bearer nonsense"}])
async def test_rejects_missing_or_invalid_token(client, xlsx_bytes, auth) -> None:
    async with client:
        response = await client.post(
            "/api/v1/upload-document",
            headers=auth,
            files={"file": ("a.xlsx", xlsx_bytes, XLSX_MIME)},
            data={"role": "knowledge_base"},
        )
    assert response.status_code == 401
    assert response.json()["type"] == "unauthenticated"


async def test_rejects_disallowed_content_type(client, settings) -> None:
    async with client:
        response = await client.post(
            "/api/v1/upload-document",
            headers=_token(settings),
            files={"file": ("virus.exe", b"MZ\x90\x00" * 20, "application/x-msdownload")},
            data={"role": "knowledge_base"},
        )
    assert response.status_code == 415
    assert response.json()["type"] == "unsupported_format"


async def test_rejects_bytes_contradicting_declared_type(client, settings) -> None:
    """A file labelled PDF whose bytes are not a PDF is broken or hostile."""
    async with client:
        response = await client.post(
            "/api/v1/upload-document",
            headers=_token(settings),
            files={"file": ("fake.pdf", b"this is not a pdf", "application/pdf")},
            data={"role": "knowledge_base"},
        )
    assert response.status_code == 415


async def test_rejects_checksum_mismatch(client, settings, xlsx_bytes) -> None:
    """A truncated upload parses into partial text, and nothing downstream can
    tell the document is incomplete.
    """
    async with client:
        response = await client.post(
            "/api/v1/upload-document",
            headers=_token(settings),
            files={"file": ("a.xlsx", xlsx_bytes, XLSX_MIME)},
            data={"role": "knowledge_base", "expected_sha256": "0" * 64},
        )
    assert response.status_code == 400
    assert response.json()["type"] == "checksum_mismatch"


async def test_accepts_matching_checksum(client, settings, xlsx_bytes) -> None:
    async with client:
        response = await client.post(
            "/api/v1/upload-document",
            headers=_token(settings),
            files={"file": ("a.xlsx", xlsx_bytes, XLSX_MIME)},
            data={
                "role": "tender",
                "expected_sha256": storage.sha256_bytes(xlsx_bytes),
            },
        )
    assert response.status_code == 202


async def test_storage_key_is_tenant_prefixed_and_filename_safe() -> None:
    key = storage.build_storage_key("t-1", "d-9", "../../etc/passwd")
    assert key.startswith("t-1/d-9/")
    # The property that matters is that no path separator survives into the
    # filename segment, so nothing can escape the tenant prefix. Literal dots
    # are harmless once "/" is gone.
    assert "/" not in key.split("/", 2)[2]
    assert "\\" not in key

    # Arabic filenames must survive intact — mangling them would make every
    # document unrecognisable to the customer who uploaded it.
    assert storage.build_storage_key("t", "d", "كراسة.pdf").endswith("كراسة.pdf")


async def test_delete_uses_the_shared_qdrant_client(monkeypatch, settings) -> None:
    """The router must hand over `app.state.qdrant`.

    Building a client inside the service opens a fresh connection pool for
    every deletion — and, more subtly, a *different* store than the one the
    application indexed into whenever the two are configured independently.
    A caller that forgets is invisible until someone counts connections.
    """
    seen: dict[str, object] = {}

    class _FakeService:
        def __init__(self, *args, **kwargs) -> None: ...

        async def delete(self, *, tenant_id, document_id, qdrant=None) -> None:
            seen["qdrant"] = qdrant

    monkeypatch.setattr(documents_router, "DocumentService", _FakeService)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(documents_router.router, prefix="/api/v1")
    sentinel = object()
    app.state.qdrant = sentinel

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.delete(
            f"/api/v1/documents/{uuid.uuid4()}", headers=_token(settings, "owner")
        )

    assert response.status_code == 204
    assert seen["qdrant"] is sentinel


@pytest.mark.parametrize("role", ["sme", "viewer"])
async def test_only_managers_may_delete_a_source(monkeypatch, settings, role) -> None:
    """Removing a source silently changes what every future answer can be
    grounded in, and leaves existing citations pointing at nothing."""

    class _FakeService:
        def __init__(self, *args, **kwargs) -> None: ...

        async def delete(self, **kwargs) -> None:
            raise AssertionError("must not be reached")

    monkeypatch.setattr(documents_router, "DocumentService", _FakeService)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(documents_router.router, prefix="/api/v1")
    app.state.qdrant = object()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.delete(
            f"/api/v1/documents/{uuid.uuid4()}", headers=_token(settings, role)
        )

    assert response.status_code == 403
