"""Persistence, against a live PostgreSQL with RLS enabled.

The question these answer is the one the product failed until now: does
anything survive a page refresh, and does it survive scoped to the right
tenant?
"""

from __future__ import annotations

import uuid

import pytest
from app.db.models.enums import (
    DocumentRole,
    DocumentStatus,
    GroundingVerdict,
    Language,
    ProposalStatus,
)
from app.db.repositories.documents import DocumentRepository
from app.db.repositories.proposals import ProposalRepository
from app.db.session import tenant_session
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _app_role(monkeypatch, app_dsn):
    """Connect as the unprivileged role so the policies actually apply."""
    monkeypatch.setenv("POSTGRES_DSN", app_dsn)
    import app.db.session as session_module
    from app.core.config import get_settings

    def _reset() -> None:
        get_settings.cache_clear()
        session_module._engine = None
        session_module._session_factory = None

    _reset()
    yield
    _reset()


@pytest.fixture
async def workspace(superuser_engine, two_tenants):
    """A workspace owned by tenant A, with its owner as the workspace owner."""
    a, _ = two_tenants
    workspace_id = uuid.uuid4()
    async with superuser_engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(a)})
        owner = (
            await conn.execute(text("SELECT id FROM users WHERE tenant_id = :t LIMIT 1"), {"t": a})
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO workspaces (id,tenant_id,name,status,response_language,"
                "owner_id,grounding_config,requirements_total,requirements_approved) "
                "VALUES (:w,:t,'Tender','draft','en',:o,'{}',0,0)"
            ),
            {"w": workspace_id, "t": a, "o": owner},
        )
    return a, workspace_id


async def test_document_row_survives_the_session(two_tenants):
    """Previously the file was indexed and no row was ever written."""
    a, _ = two_tenants
    async with tenant_session(a) as session:
        created = await DocumentRepository(session).create(
            tenant_id=a,
            filename="كراسة الشروط.pdf",
            mime_type="application/pdf",
            size_bytes=1024,
            content_sha256="a" * 64,
            storage_key=f"{a}/doc/كراسة.pdf",
            role=DocumentRole.KNOWLEDGE_BASE,
        )
        document_id = created.id

    # A completely separate session — as a page refresh would be.
    async with tenant_session(a) as session:
        found = await DocumentRepository(session).get(document_id)

    assert found is not None
    assert found.filename == "كراسة الشروط.pdf"
    assert found.status is DocumentStatus.UPLOADED


async def test_documents_are_invisible_to_another_tenant(two_tenants):
    a, b = two_tenants
    async with tenant_session(a) as session:
        created = await DocumentRepository(session).create(
            tenant_id=a,
            filename="private.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            content_sha256="b" * 64,
            storage_key="k",
            role=DocumentRole.KNOWLEDGE_BASE,
        )
        document_id = created.id

    async with tenant_session(b) as session:
        assert await DocumentRepository(session).get(document_id) is None
        rows, total = await DocumentRepository(session).list()
    assert rows == [] and total == 0


async def test_ingestion_result_is_recorded(two_tenants):
    a, _ = two_tenants
    async with tenant_session(a) as session:
        created = await DocumentRepository(session).create(
            tenant_id=a,
            filename="scan.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            content_sha256="c" * 64,
            storage_key="k",
            role=DocumentRole.KNOWLEDGE_BASE,
        )
        document_id = created.id

    async with tenant_session(a) as session:
        await DocumentRepository(session).record_ingestion_result(
            document_id,
            chunk_count=42,
            page_count=214,
            language=Language.AR,
            parse_strategy=None,
            text_extraction_ratio=0.72,
            metadata={"table_count": 3},
        )

    async with tenant_session(a) as session:
        document = await DocumentRepository(session).get(document_id)
    assert document.status is DocumentStatus.READY
    assert document.chunk_count == 42
    # The Arabic-scan quality signal must survive to the UI.
    assert document.text_extraction_ratio == pytest.approx(0.72)
    assert document.doc_metadata["table_count"] == 3


async def test_failed_document_must_carry_a_reason(two_tenants):
    """A CHECK constraint enforces it — a failure with no reason is a support
    ticket nobody can answer."""
    a, _ = two_tenants
    async with tenant_session(a) as session:
        created = await DocumentRepository(session).create(
            tenant_id=a,
            filename="broken.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            content_sha256="d" * 64,
            storage_key="k",
            role=DocumentRole.KNOWLEDGE_BASE,
        )
        document_id = created.id

    with pytest.raises(Exception, match="(?i)check|constraint"):
        async with tenant_session(a) as session:
            await DocumentRepository(session).set_status(document_id, DocumentStatus.FAILED)


async def test_proposal_survives_and_versions_on_regeneration(workspace):
    """Regeneration supersedes rather than overwrites.

    A submitted response must stay reconstructible exactly as approved, which
    an in-place UPDATE destroys.
    """
    tenant_id, workspace_id = workspace

    async def _save(answer: str) -> None:
        async with tenant_session(tenant_id) as session:
            await ProposalRepository(session).save_generation(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                requirement_ref="3.2.14",
                requirement_text="Hold ISO 27001?",
                answer_text=answer,
                language=Language.EN,
                status=ProposalStatus.DRAFT,
                citations=[{"chunk_id": "c1", "document_name": "cert.pdf"}],
                retrieved_chunk_ids=["c1"],
                grounding_verdict=GroundingVerdict.VERIFIED,
                citation_coverage=1.0,
                top_retrieval_score=0.93,
                confidence_score=0.94,
                abstention_reason=None,
                model_id="claude-opus-5",
                prompt_version="v1",
            )

    await _save("First answer.")
    await _save("Second answer.")

    async with tenant_session(tenant_id) as session:
        repository = ProposalRepository(session)
        current = await repository.get_current(workspace_id=workspace_id, requirement_ref="3.2.14")
        versions = await repository.history(workspace_id=workspace_id, requirement_ref="3.2.14")

    assert current.answer_text == "Second answer."
    assert current.version == 2
    assert len(versions) == 2
    # The superseded version keeps its text and its citations.
    assert versions[1].answer_text == "First answer."
    assert versions[1].is_current is False
    assert versions[1].citations[0]["document_name"] == "cert.pdf"


async def test_only_one_version_can_be_current(workspace):
    """Enforced by the partial unique index, not by application discipline."""
    tenant_id, workspace_id = workspace
    async with tenant_session(tenant_id) as session:
        await ProposalRepository(session).save_generation(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            requirement_ref="9.1",
            requirement_text="Q",
            answer_text="A",
            language=Language.EN,
            status=ProposalStatus.DRAFT,
            citations=[],
            retrieved_chunk_ids=[],
            grounding_verdict=GroundingVerdict.VERIFIED,
            citation_coverage=1.0,
            top_retrieval_score=0.9,
            confidence_score=0.9,
            abstention_reason=None,
            model_id="m",
            prompt_version="v1",
        )

    with pytest.raises(Exception, match="(?i)unique|duplicate"):
        async with tenant_session(tenant_id) as session:
            await session.execute(
                text(
                    "INSERT INTO generated_proposals "
                    "(id,tenant_id,workspace_id,requirement_ref,requirement_text,"
                    " is_mandatory,language,citations,retrieved_chunk_ids,"
                    " grounding_verdict,status,version,is_current) "
                    "VALUES (gen_random_uuid(),:t,:w,'9.1','Q',true,'en','[]','[]',"
                    " 'verified','draft',1,true)"
                ),
                {"t": tenant_id, "w": workspace_id},
            )


async def test_approval_requires_a_named_reviewer(workspace):
    """The core product invariant, enforced by a database CHECK.

    No code path — present or future — can produce an approved answer without
    someone accountable for it.
    """
    tenant_id, workspace_id = workspace
    with pytest.raises(Exception, match="(?i)check|constraint"):
        async with tenant_session(tenant_id) as session:
            await session.execute(
                text(
                    "INSERT INTO generated_proposals "
                    "(id,tenant_id,workspace_id,requirement_ref,requirement_text,"
                    " is_mandatory,answer_text,language,citations,retrieved_chunk_ids,"
                    " grounding_verdict,status,version,is_current) "
                    "VALUES (gen_random_uuid(),:t,:w,'5.5','Q',true,'A','en','[]','[]',"
                    " 'verified','approved',1,true)"
                ),
                {"t": tenant_id, "w": workspace_id},
            )


async def test_progress_counts_only_mandatory_requirements(workspace):
    """The export gate turns on required items; counting optional ones would
    block a submission that is actually complete."""
    tenant_id, workspace_id = workspace

    async def _save(ref: str, mandatory: bool) -> None:
        async with tenant_session(tenant_id) as session:
            await ProposalRepository(session).save_generation(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                requirement_ref=ref,
                requirement_text="Q",
                is_mandatory=mandatory,
                answer_text="A",
                language=Language.EN,
                status=ProposalStatus.DRAFT,
                citations=[],
                retrieved_chunk_ids=[],
                grounding_verdict=GroundingVerdict.VERIFIED,
                citation_coverage=1.0,
                top_retrieval_score=0.9,
                confidence_score=0.9,
                abstention_reason=None,
                model_id="m",
                prompt_version="v1",
            )

    await _save("1.1", True)
    await _save("1.2", True)
    await _save("9.9", False)

    async with tenant_session(tenant_id) as session:
        approved, total = await ProposalRepository(session).count_approved(workspace_id)
    assert (approved, total) == (0, 2)
