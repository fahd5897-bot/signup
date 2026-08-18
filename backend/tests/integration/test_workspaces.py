"""Tender workspaces, against live PostgreSQL with RLS enforced.

Without these endpoints there is no way to start a pursuit at all, so the
things worth proving are the boundaries: a workspace belongs to exactly one
tenant, its owner is the authenticated caller and not whoever the body claims,
and the review counts a bid manager reads agree with the export gate.
"""

from __future__ import annotations

import uuid

import pytest
from app.core.exceptions import TenantMismatchError
from app.db.models.enums import (
    ChunkType,
    GroundingVerdict,
    Language,
    ProposalStatus,
    UserRole,
    WorkspaceStatus,
)
from app.db.repositories.proposals import ProposalRepository
from app.db.session import tenant_session
from app.schemas.proposal import Citation
from app.schemas.workspace import WorkspaceCreate
from app.services.review_service import ReviewService
from app.services.workspace_service import WorkspaceService
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _app_role(monkeypatch, app_dsn):
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
async def owners(superuser_engine, two_tenants):
    """The owning user of each tenant."""
    a, b = two_tenants
    ids = {}
    async with superuser_engine.begin() as conn:
        for tenant_id in (a, b):
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
            )
            ids[tenant_id] = (
                await conn.execute(
                    text("SELECT id FROM users WHERE tenant_id = :t LIMIT 1"), {"t": tenant_id}
                )
            ).scalar_one()
    return ids


async def _create(tenant_id: uuid.UUID, owner_id: uuid.UUID, name: str = "MOH/2026/IT/0114"):
    return await WorkspaceService().create(
        tenant_id=tenant_id,
        owner_id=owner_id,
        payload=WorkspaceCreate(name=name, response_language=Language.EN),
    )


async def test_a_new_pursuit_starts_empty_and_owned_by_its_creator(two_tenants, owners):
    tenant_id, _ = two_tenants
    workspace = await _create(tenant_id, owners[tenant_id])

    assert workspace.tenant_id == tenant_id
    assert workspace.owner_id == owners[tenant_id]
    assert workspace.status is WorkspaceStatus.DRAFT
    assert workspace.requirements_total == 0
    # The grounding policy is snapshotted, not read live from tenant settings:
    # tightening it next quarter must not retroactively change the confidence
    # figures a reviewer already signed off on.
    assert workspace.grounding_config["require_human_approval"] is True


async def test_human_approval_cannot_be_switched_off_at_creation(two_tenants, owners):
    """The product's core claim is that a human signs every answer, so the
    schema refuses the payload rather than the service refusing the write."""
    with pytest.raises(ValueError, match="human approval"):
        WorkspaceCreate(
            name="Shortcut",
            grounding_config={"require_human_approval": False},
        )


async def test_a_pursuit_is_invisible_to_another_tenant(two_tenants, owners):
    a, b = two_tenants
    workspace = await _create(a, owners[a])

    with pytest.raises(TenantMismatchError):
        await WorkspaceService().get(tenant_id=b, workspace_id=workspace.id)

    listed, total = await WorkspaceService().list(tenant_id=b)
    assert listed == [] and total == 0


async def test_the_list_reports_counts_that_agree_with_the_export_gate(two_tenants, owners):
    """The dashboard progress bar and the export gate must not disagree — a
    reviewer told they are done, then refused at export, stops trusting both."""
    tenant_id, _ = two_tenants
    owner_id = owners[tenant_id]
    workspace = await _create(tenant_id, owner_id)

    citation = Citation(
        chunk_id="c1",
        document_id=uuid.uuid4(),
        document_name="iso-27001.pdf",
        page_number=4,
        chunk_type=ChunkType.NARRATIVE,
        quoted_text="Certificate 12345.",
    ).model_dump(mode="json")

    async with tenant_session(tenant_id) as session:
        repository = ProposalRepository(session)
        for ref, mandatory in (("1.1", True), ("1.2", True), ("1.9", False)):
            await repository.save_generation(
                tenant_id=tenant_id,
                workspace_id=workspace.id,
                requirement_ref=ref,
                requirement_text=f"Requirement {ref}",
                answer_text="Yes.",
                language=Language.EN,
                status=ProposalStatus.DRAFT,
                citations=[citation],
                retrieved_chunk_ids=["c1"],
                grounding_verdict=GroundingVerdict.VERIFIED,
                citation_coverage=1.0,
                top_retrieval_score=0.9,
                confidence_score=0.9,
                abstention_reason=None,
                model_id="claude-opus-5",
                prompt_version="answer-gen/2026-08-17",
                is_mandatory=mandatory,
            )

    listed, _ = await WorkspaceService().list(tenant_id=tenant_id)
    progress = await ReviewService().progress(tenant_id=tenant_id, workspace_id=workspace.id)

    # Mandatory only, in both places — the optional item is excluded from the
    # gate, so counting it on the dashboard would show a bar that never fills.
    assert listed[0].requirements_total == progress.total == 2
    assert listed[0].requirements_approved == progress.approved == 0
    assert listed[0].is_exportable is False


async def test_counts_follow_an_approval(two_tenants, owners):
    tenant_id, _ = two_tenants
    owner_id = owners[tenant_id]
    workspace = await _create(tenant_id, owner_id)

    async with tenant_session(tenant_id) as session:
        proposal = await ProposalRepository(session).save_generation(
            tenant_id=tenant_id,
            workspace_id=workspace.id,
            requirement_ref="1.1",
            requirement_text="Do you hold ISO 27001?",
            answer_text="Yes.",
            language=Language.EN,
            status=ProposalStatus.DRAFT,
            citations=[
                Citation(
                    chunk_id="c1",
                    document_id=uuid.uuid4(),
                    document_name="iso.pdf",
                    chunk_type=ChunkType.NARRATIVE,
                    quoted_text="Certificate 12345.",
                ).model_dump(mode="json")
            ],
            retrieved_chunk_ids=["c1"],
            grounding_verdict=GroundingVerdict.VERIFIED,
            citation_coverage=1.0,
            top_retrieval_score=0.9,
            confidence_score=0.9,
            abstention_reason=None,
            model_id="claude-opus-5",
            prompt_version="answer-gen/2026-08-17",
        )
        proposal_id = proposal.id

    await ReviewService().apply_action(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        actor_id=owner_id,
        actor_role=UserRole.OWNER,
        action=ProposalStatus.APPROVED,
        expected_version=1,
        review_notes="checked",
    )

    listed, _ = await WorkspaceService().list(tenant_id=tenant_id)
    assert listed[0].requirements_approved == 1
    assert listed[0].is_exportable is True
