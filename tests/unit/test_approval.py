"""Tests for the approval engine: create/resolve/receipt, hash binding, expiry."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from personal_ai_os.common.utils import argument_hash
from personal_ai_os.db import session as db_session
from personal_ai_os.db.models import Run, User
from personal_ai_os.db.session import session_scope
from personal_ai_os.policy_engine import ApprovalEngine
from personal_ai_os.policy_engine.approval import (
    ApprovalAuthMethodRequiredError,
    ApprovalExpiredError,
    ApprovalInvalidDecisionError,
    ApprovalNotFoundError,
    ApprovalNotPendingError,
)

ARGS = {"to": "external@x.com", "subject": "hello"}


@pytest.fixture
def make_request(user_run):
    owner_id, run_id = user_run

    def _make(**overrides):
        kwargs = dict(
            run_id=run_id,
            session_id=None,
            owner_id=owner_id,
            action_summary="Send email to external@x.com",
            tool_name="email.send",
            arguments_preview=dict(ARGS),
            risk_level=3,
            risk_reason="External communication",
        )
        kwargs.update(overrides)
        return kwargs

    return _make


@pytest.fixture
def owner_id(user_run) -> object:
    """A real user id (users table has FK on approved_by)."""
    return user_run[0]


@pytest.fixture
def engine() -> ApprovalEngine:
    return ApprovalEngine()


async def test_create_request_persists(engine, make_request):
    request = await engine.create_request(**make_request())
    assert request.id is not None
    assert request.status == "pending"
    assert request.tool_name == "email.send"
    assert request.risk_level == 3

    fetched = await engine.get_request(request.id)
    assert fetched is not None
    assert fetched.id == request.id
    assert fetched.arguments_preview == ARGS


async def test_approved_receipt_and_verify(engine, make_request, owner_id):
    request = await engine.create_request(**make_request())

    receipt = await engine.resolve(request.id, decision="approved", approved_by=owner_id)
    assert receipt.approval_id == request.id
    assert receipt.decision == "approved"
    assert receipt.scope == "single_action"
    assert receipt.tool_name == "email.send"
    assert receipt.approved_by == owner_id
    assert receipt.argument_hash == argument_hash(ARGS)

    assert await engine.verify(receipt, "email.send", dict(ARGS)) is True
    assert await engine.get_status(request.id) == "approved"


async def test_argument_hash_prevents_argument_swap(engine, make_request, owner_id):
    request = await engine.create_request(**make_request())
    receipt = await engine.resolve(request.id, decision="approved", approved_by=owner_id)

    # approved A, executed B -> rejected by hash check
    tampered = dict(ARGS)
    tampered["to"] = "attacker@evil.com"
    assert await engine.verify(receipt, "email.send", tampered) is False

    # approved email.send, executing another tool -> rejected
    assert await engine.verify(receipt, "github.push", dict(ARGS)) is False


async def test_approved_with_edits_binds_edited_arguments(engine, make_request, owner_id):
    request = await engine.create_request(**make_request())
    edited = {"to": "edited@x.com", "subject": "edited"}
    receipt = await engine.resolve(
        request.id, decision="approved_with_edits", approved_by=owner_id, edited_arguments=edited
    )

    assert receipt.decision == "approved_with_edits"
    assert receipt.argument_hash == argument_hash(edited)
    assert await engine.verify(receipt, "email.send", edited) is True
    # original (un-edited) arguments no longer match the receipt
    assert await engine.verify(receipt, "email.send", dict(ARGS)) is False
    assert await engine.get_status(request.id) == "edited"


async def test_edited_approval_persists_bound_hash_for_execution(engine, make_request, owner_id):
    """P0-003 — resolve() must persist edited args+hash so the execution-time
    guard (verify_approval) accepts the edited call and rejects the original."""
    request = await engine.create_request(**make_request())
    edited = {"to": "edited@x.com", "subject": "edited"}
    await engine.resolve(
        request.id, decision="approved_with_edits", approved_by=owner_id, edited_arguments=edited
    )
    # DB-backed execution guard reads the persisted row
    assert await engine.verify_approval(request.id, "email.send", edited) is True
    assert await engine.verify_approval(request.id, "email.send", dict(ARGS)) is False


async def test_reject_sets_status_and_receipt_is_not_verifiable(engine, make_request, owner_id):
    request = await engine.create_request(**make_request())
    receipt = await engine.resolve(request.id, decision="rejected", approved_by=owner_id)

    assert await engine.get_status(request.id) == "rejected"
    assert receipt.decision == "rejected"
    assert await engine.verify(receipt, "email.send", dict(ARGS)) is False


async def test_passkey_approval_fails_closed_without_verifier(
    engine, make_request, owner_id
):
    request = await engine.create_request(
        **make_request(risk_level=4, requires_auth_method="passkey")
    )

    with pytest.raises(ApprovalAuthMethodRequiredError, match="passkey"):
        await engine.resolve(request.id, decision="approved", approved_by=owner_id)

    assert await engine.get_status(request.id) == "pending"
    assert await engine.verify_approval(request.id, "email.send", dict(ARGS)) is False


async def test_expired_request_auto_expires_and_cannot_resolve(engine, make_request, owner_id):
    request = await engine.create_request(**make_request(expires_at=datetime.now(UTC) - timedelta(seconds=1)))

    with pytest.raises(ApprovalExpiredError, match="expired"):
        await engine.resolve(request.id, decision="approved", approved_by=owner_id)

    assert await engine.get_status(request.id) == "expired"


async def test_unknown_decision_and_double_resolve(engine, make_request, owner_id):
    request = await engine.create_request(**make_request())

    with pytest.raises(ApprovalInvalidDecisionError, match="Unknown approval decision"):
        await engine.resolve(request.id, decision="maybe", approved_by=owner_id)

    await engine.resolve(request.id, decision="approved", approved_by=owner_id)
    with pytest.raises(ApprovalNotPendingError, match="already approved"):
        await engine.resolve(request.id, decision="approved", approved_by=owner_id)


async def test_concurrent_resolve_has_one_cas_winner(tmp_path):
    await db_session.dispose_engine()
    db_session.configure(f"sqlite+aiosqlite:///{tmp_path / 'approval-cas.db'}")
    await db_session.reset_database()

    async with session_scope() as session:
        user = User(username=f"cas-{uuid4().hex[:8]}")
        session.add(user)
        await session.flush()
        run = Run(owner_id=user.id, status="waiting_approval", input={})
        session.add(run)
        await session.flush()
        owner_id, run_id = user.id, run.id

    engine = ApprovalEngine()
    request = await engine.create_request(
        run_id=run_id,
        session_id=None,
        owner_id=owner_id,
        action_summary="Send email",
        tool_name="email.send",
        arguments_preview=dict(ARGS),
        risk_level=3,
        risk_reason="External communication",
    )
    outcomes = await asyncio.gather(
        engine.resolve(request.id, decision="approved", approved_by=owner_id),
        engine.resolve(request.id, decision="rejected", approved_by=owner_id),
        return_exceptions=True,
    )

    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, ApprovalNotPendingError) for outcome in outcomes) == 1
    assert await engine.get_status(request.id) in {"approved", "rejected"}


async def test_missing_approval_raises(engine):
    with pytest.raises(ApprovalNotFoundError, match="not found"):
        await engine.resolve(uuid4(), decision="approved", approved_by=uuid4())

    assert await engine.get_request(uuid4()) is None
