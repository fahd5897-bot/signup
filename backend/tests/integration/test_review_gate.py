"""The human-in-the-loop gate, against a live PostgreSQL with RLS enabled.

This is the module that decides what the product promises. Every test here is
a statement about something that must not be possible: approving an answer
with no evidence, approving text you never read, an SME signing off their own
work, or an approval surviving an edit to the words it approved.
"""

from __future__ import annotations

import uuid

import pytest
from app.core.exceptions import (
    InvalidTransitionError,
    PermissionDeniedError,
    TenantMismatchError,
    UngroundedApprovalError,
    VersionConflictError,
)
from app.db.models.enums import ChunkType, GroundingVerdict, Language, ProposalStatus, UserRole
from app.db.repositories.proposals import ProposalRepository
from app.db.session import tenant_session
from app.schemas.proposal import Citation, ProposalRead
from app.services.review_service import ReviewService
from sqlalchemy import text

pytestmark = pytest.mark.integration


def _citation(**overrides) -> dict:
    """Build a citation through the production model, not by hand.

    A hand-written dict drifts from `Citation` silently — the JSONB column
    accepts anything, so the row stores fine and only fails much later when a
    response model tries to serialise it. Going through the model means the
    fixture cannot describe a citation the application would never write.
    """
    payload = {
        "chunk_id": "c1",
        "document_id": uuid.uuid4(),
        "document_name": "iso-27001.pdf",
        "page_number": 4,
        "chunk_type": ChunkType.NARRATIVE,
        "quoted_text": "Certificate 12345 — ISO/IEC 27001:2022, valid until 2027.",
        "relevance_score": 0.94,
    }
    payload.update(overrides)
    return Citation(**payload).model_dump(mode="json")


CITED = [_citation()]


@pytest.fixture(autouse=True)
def _app_role(monkeypatch, app_dsn):
    """Connect as the unprivileged role, so the policies actually apply.

    Without the cache clear the settings object built by an earlier test
    survives, the suite connects as the superuser, and every isolation
    assertion below passes while proving nothing.
    """
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
    """A workspace in tenant A, plus its owner and an SME."""
    a, _ = two_tenants
    workspace_id = uuid.uuid4()
    sme_id = uuid.uuid4()
    async with superuser_engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(a)})
        owner_id = (
            await conn.execute(text("SELECT id FROM users WHERE tenant_id = :t LIMIT 1"), {"t": a})
        ).scalar_one()
        await conn.execute(
            text(
                "INSERT INTO users (id,tenant_id,email,full_name,role,is_active,locale) "
                "VALUES (:u,:t,:e,'Expert','sme',true,'en')"
            ),
            {"u": sme_id, "t": a, "e": f"sme-{sme_id.hex[:8]}@acme.com"},
        )
        await conn.execute(
            text(
                "INSERT INTO workspaces (id,tenant_id,name,status,response_language,"
                "owner_id,grounding_config,requirements_total,requirements_approved) "
                "VALUES (:w,:t,'Tender','draft','en',:o,'{}',0,0)"
            ),
            {"w": workspace_id, "t": a, "o": owner_id},
        )
    return a, workspace_id, owner_id, sme_id


async def _draft(
    tenant_id: uuid.UUID,
    workspace_id: uuid.UUID,
    *,
    ref: str = "3.2.14",
    answer: str | None = "We hold ISO 27001, certificate 12345.",
    citations: list[dict] | None = None,
    status: ProposalStatus = ProposalStatus.DRAFT,
    is_mandatory: bool = True,
    abstention_reason: str | None = None,
) -> uuid.UUID:
    async with tenant_session(tenant_id) as session:
        proposal = await ProposalRepository(session).save_generation(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            requirement_ref=ref,
            requirement_text="Do you hold ISO 27001?",
            answer_text=answer,
            language=Language.EN,
            status=status,
            citations=CITED if citations is None else citations,
            retrieved_chunk_ids=["c1"],
            grounding_verdict=GroundingVerdict.VERIFIED,
            citation_coverage=1.0,
            top_retrieval_score=0.93,
            confidence_score=0.94,
            abstention_reason=abstention_reason,
            model_id="claude-opus-5",
            prompt_version="v1",
            is_mandatory=is_mandatory,
        )
        return proposal.id


# --------------------------------------------------------------- the gate
async def test_approval_records_a_named_human_and_advances_the_revision(workspace):
    tenant_id, workspace_id, owner_id, _ = workspace
    proposal_id = await _draft(tenant_id, workspace_id)

    approved = await ReviewService().apply_action(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        actor_id=owner_id,
        actor_role=UserRole.OWNER,
        action=ProposalStatus.APPROVED,
        expected_version=1,
        review_notes="Checked against the certificate.",
    )

    assert approved.status is ProposalStatus.APPROVED
    assert approved.reviewed_by_id == owner_id
    assert approved.reviewed_at is not None
    # The revision must move, or a second reviewer holding version 1 would
    # still pass the freshness check and overwrite this decision.
    assert approved.version == 2

    # Serialise it exactly as the endpoint does, after the session has closed.
    # `updated_at` is computed by PostgreSQL on UPDATE, so the returned row
    # carries an attribute SQLAlchemy wants to re-fetch — and on a detached
    # instance that raises, turning a successful approval into a 500. Asserting
    # on the ORM fields alone never touches it and reports green.
    body = ProposalRead.model_validate(approved)
    assert body.status is ProposalStatus.APPROVED
    assert body.updated_at is not None


async def test_uncited_answer_cannot_be_approved(workspace):
    """The golden rule: no evidence, no approval."""
    tenant_id, workspace_id, owner_id, _ = workspace
    proposal_id = await _draft(tenant_id, workspace_id, citations=[])

    with pytest.raises(UngroundedApprovalError) as excinfo:
        await ReviewService().apply_action(
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            actor_id=owner_id,
            actor_role=UserRole.OWNER,
            action=ProposalStatus.APPROVED,
            expected_version=1,
        )
    assert excinfo.value.context["reason"] == "no_citations"

    async with tenant_session(tenant_id) as session:
        stored = await ProposalRepository(session).get_by_id(proposal_id)
    assert stored.status is ProposalStatus.DRAFT


async def test_uncited_answer_may_be_approved_only_with_an_explicit_written_owning(workspace):
    """A reviewer writing the answer themselves is legitimate — but it must be
    distinguishable in the audit trail, so it costs a flag *and* a note."""
    tenant_id, workspace_id, owner_id, _ = workspace
    proposal_id = await _draft(tenant_id, workspace_id, citations=[])
    service = ReviewService()

    # Flag without notes is still refused: an unexplained override is exactly
    # the thing an auditor cannot evaluate.
    with pytest.raises(UngroundedApprovalError) as excinfo:
        await service.apply_action(
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            actor_id=owner_id,
            actor_role=UserRole.OWNER,
            action=ProposalStatus.APPROVED,
            expected_version=1,
            acknowledge_ungrounded=True,
        )
    assert excinfo.value.context["reason"] == "acknowledgement_without_notes"

    approved = await service.apply_action(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        actor_id=owner_id,
        actor_role=UserRole.OWNER,
        action=ProposalStatus.APPROVED,
        expected_version=1,
        acknowledge_ungrounded=True,
        review_notes="Written by me from the signed contract; no digital source.",
    )
    assert approved.status is ProposalStatus.APPROVED


async def test_empty_answer_cannot_be_approved(workspace):
    tenant_id, workspace_id, owner_id, _ = workspace
    proposal_id = await _draft(tenant_id, workspace_id, answer=None)

    with pytest.raises(UngroundedApprovalError) as excinfo:
        await ReviewService().apply_action(
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            actor_id=owner_id,
            actor_role=UserRole.OWNER,
            action=ProposalStatus.APPROVED,
            expected_version=1,
        )
    assert excinfo.value.context["reason"] == "empty"


async def test_abstention_cannot_be_approved_at_all(workspace):
    """An abstention has no text and no evidence; the only ways out are an
    edit or an escalation."""
    tenant_id, workspace_id, owner_id, _ = workspace
    proposal_id = await _draft(
        tenant_id,
        workspace_id,
        answer=None,
        citations=[],
        status=ProposalStatus.ABSTAINED,
        abstention_reason="no chunk cleared the retrieval threshold",
    )

    with pytest.raises(InvalidTransitionError):
        await ReviewService().apply_action(
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            actor_id=owner_id,
            actor_role=UserRole.OWNER,
            action=ProposalStatus.APPROVED,
            expected_version=1,
            acknowledge_ungrounded=True,
            review_notes="trying to force it through",
        )


# ------------------------------------------------------------- concurrency
async def test_a_stale_decision_is_refused_not_merged(workspace):
    """Two reviewers open the same answer; the second must not silently
    overwrite the first's decision against text they never saw."""
    tenant_id, workspace_id, owner_id, _ = workspace
    proposal_id = await _draft(tenant_id, workspace_id)
    service = ReviewService()

    await service.apply_action(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        actor_id=owner_id,
        actor_role=UserRole.OWNER,
        action=ProposalStatus.PENDING_REVIEW,
        expected_version=1,
    )

    with pytest.raises(VersionConflictError) as excinfo:
        await service.apply_action(
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            actor_id=owner_id,
            actor_role=UserRole.OWNER,
            action=ProposalStatus.APPROVED,
            expected_version=1,  # the snapshot the second reviewer still holds
        )
    assert excinfo.value.context["current_version"] == 2


# ------------------------------------------------------------------- roles
async def test_an_sme_cannot_sign_off_their_own_work(workspace):
    tenant_id, workspace_id, owner_id, sme_id = workspace
    proposal_id = await _draft(tenant_id, workspace_id)
    service = ReviewService()

    await service.apply_action(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        actor_id=owner_id,
        actor_role=UserRole.OWNER,
        action=ProposalStatus.NEEDS_SME,
        expected_version=1,
        assigned_sme_id=sme_id,
    )

    with pytest.raises(PermissionDeniedError):
        await service.apply_action(
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            actor_id=sme_id,
            actor_role=UserRole.SME,
            action=ProposalStatus.APPROVED,
            expected_version=2,
        )

    # They can hand it back for review, which is their actual job.
    handed_back = await service.apply_action(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        actor_id=sme_id,
        actor_role=UserRole.SME,
        action=ProposalStatus.PENDING_REVIEW,
        expected_version=2,
    )
    assert handed_back.status is ProposalStatus.PENDING_REVIEW


async def test_an_sme_cannot_touch_an_answer_assigned_to_someone_else(workspace):
    tenant_id, workspace_id, owner_id, sme_id = workspace
    proposal_id = await _draft(tenant_id, workspace_id)

    with pytest.raises(PermissionDeniedError):
        await ReviewService().apply_action(
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            actor_id=sme_id,
            actor_role=UserRole.SME,
            action=ProposalStatus.PENDING_REVIEW,
            expected_version=1,
        )


async def test_a_viewer_reviews_nothing(workspace):
    tenant_id, workspace_id, _, _ = workspace
    proposal_id = await _draft(tenant_id, workspace_id)

    with pytest.raises(PermissionDeniedError):
        await ReviewService().apply_action(
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            actor_id=uuid.uuid4(),
            actor_role=UserRole.VIEWER,
            action=ProposalStatus.APPROVED,
            expected_version=1,
        )


# -------------------------------------------------------------------- edits
async def test_editing_an_approved_answer_revokes_the_approval(workspace):
    """Otherwise the record shows a named human approving words that did not
    exist when they clicked approve."""
    tenant_id, workspace_id, owner_id, _ = workspace
    proposal_id = await _draft(tenant_id, workspace_id)
    service = ReviewService()

    await service.apply_action(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        actor_id=owner_id,
        actor_role=UserRole.OWNER,
        action=ProposalStatus.APPROVED,
        expected_version=1,
        review_notes="ok",
    )

    edited = await service.apply_edit(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        actor_id=owner_id,
        actor_role=UserRole.OWNER,
        edited_text="We hold ISO 27001 and ISO 9001.",
        expected_version=2,
    )

    assert ProposalRead.model_validate(edited).final_text == "We hold ISO 27001 and ISO 9001."
    assert edited.status is ProposalStatus.PENDING_REVIEW
    assert edited.reviewed_by_id is None
    assert edited.reviewed_at is None
    # The generated original survives, so the human-vs-model delta stays
    # measurable.
    assert edited.answer_text == "We hold ISO 27001, certificate 12345."
    assert edited.edited_text == "We hold ISO 27001 and ISO 9001."


async def test_an_edit_supplies_the_text_an_abstention_lacks(workspace):
    """The escape hatch that keeps a fail-closed abstention from permanently
    blocking a submission."""
    tenant_id, workspace_id, owner_id, _ = workspace
    proposal_id = await _draft(
        tenant_id,
        workspace_id,
        answer=None,
        citations=[],
        status=ProposalStatus.ABSTAINED,
        abstention_reason="no chunk cleared the retrieval threshold",
    )
    service = ReviewService()

    await service.apply_edit(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        actor_id=owner_id,
        actor_role=UserRole.OWNER,
        edited_text="Answered manually from the signed framework agreement.",
        expected_version=1,
    )
    await service.apply_action(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        actor_id=owner_id,
        actor_role=UserRole.OWNER,
        action=ProposalStatus.PENDING_REVIEW,
        expected_version=2,
    )
    approved = await service.apply_action(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        actor_id=owner_id,
        actor_role=UserRole.OWNER,
        action=ProposalStatus.APPROVED,
        expected_version=3,
        acknowledge_ungrounded=True,
        review_notes="Sourced from the signed agreement, which is not in the corpus.",
    )
    assert approved.status is ProposalStatus.APPROVED


# ----------------------------------------------------------- the export gate
async def test_export_is_blocked_until_every_mandatory_answer_is_signed_off(workspace):
    tenant_id, workspace_id, owner_id, _ = workspace
    await _draft(tenant_id, workspace_id, ref="1.1")
    await _draft(tenant_id, workspace_id, ref="1.2")
    # Optional items must not hold up a submission that is actually complete.
    await _draft(tenant_id, workspace_id, ref="1.3", is_mandatory=False)
    service = ReviewService()

    empty = await service.progress(tenant_id=tenant_id, workspace_id=uuid.uuid4())
    assert empty.ready_to_export is False, "an empty workspace has nothing signed off"

    progress = await service.progress(tenant_id=tenant_id, workspace_id=workspace_id)
    assert (progress.approved, progress.total, progress.outstanding) == (0, 2, 2)
    assert progress.ready_to_export is False

    async with tenant_session(tenant_id) as session:
        repository = ProposalRepository(session)
        pending, _ = await repository.list_for_review(workspace_id=workspace_id)
        mandatory = [p.id for p in pending if p.is_mandatory]

    for proposal_id in mandatory:
        await service.apply_action(
            tenant_id=tenant_id,
            proposal_id=proposal_id,
            actor_id=owner_id,
            actor_role=UserRole.OWNER,
            action=ProposalStatus.APPROVED,
            expected_version=1,
            review_notes="ok",
        )

    final = await service.progress(tenant_id=tenant_id, workspace_id=workspace_id)
    assert (final.approved, final.total, final.outstanding) == (2, 2, 0)
    assert final.ready_to_export is True


# ---------------------------------------------------------------- isolation
async def test_another_tenants_proposal_is_not_found_rather_than_forbidden(workspace, two_tenants):
    """404, not 403 — a 403 confirms the id exists and turns the endpoint into
    an enumeration oracle across tenants."""
    tenant_id, workspace_id, _, _ = workspace
    _, other_tenant = two_tenants
    proposal_id = await _draft(tenant_id, workspace_id)

    with pytest.raises(TenantMismatchError):
        await ReviewService().get(tenant_id=other_tenant, proposal_id=proposal_id)

    with pytest.raises(TenantMismatchError):
        await ReviewService().apply_action(
            tenant_id=other_tenant,
            proposal_id=proposal_id,
            actor_id=uuid.uuid4(),
            actor_role=UserRole.OWNER,
            action=ProposalStatus.APPROVED,
            expected_version=1,
        )
