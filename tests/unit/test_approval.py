"""Tests for the approval engine: create/resolve/receipt, hash binding, expiry."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from personal_ai_os.common.utils import argument_hash
from personal_ai_os.policy_engine import ApprovalEngine

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
def owner_id(user_run) -> "object":
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


async def test_reject_sets_status_and_receipt_is_not_verifiable(engine, make_request, owner_id):
    request = await engine.create_request(**make_request())
    receipt = await engine.resolve(request.id, decision="rejected", approved_by=owner_id)

    assert await engine.get_status(request.id) == "rejected"
    assert receipt.decision == "rejected"
    assert await engine.verify(receipt, "email.send", dict(ARGS)) is False


async def test_expired_request_auto_expires_and_cannot_resolve(engine, make_request, owner_id):
    request = await engine.create_request(**make_request(expires_at=datetime.utcnow() - timedelta(seconds=1)))

    fetched = await engine.get_request(request.id)
    assert fetched.status == "expired"

    with pytest.raises(ValueError, match="expired"):
        await engine.resolve(request.id, decision="approved", approved_by=owner_id)


async def test_unknown_decision_and_double_resolve(engine, make_request, owner_id):
    request = await engine.create_request(**make_request())

    with pytest.raises(ValueError, match="Unknown approval decision"):
        await engine.resolve(request.id, decision="maybe", approved_by=owner_id)

    await engine.resolve(request.id, decision="approved", approved_by=owner_id)
    with pytest.raises(ValueError, match="already approved"):
        await engine.resolve(request.id, decision="approved", approved_by=owner_id)


async def test_missing_approval_raises(engine):
    with pytest.raises(ValueError, match="not found"):
        await engine.resolve(uuid4(), decision="approved", approved_by=uuid4())

    assert await engine.get_request(uuid4()) is None
