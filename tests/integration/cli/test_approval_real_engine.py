"""C1/C2/M2 regression tests — approval APIs against the REAL engine.

The pre-v2 approvals router first-call-409'd and resume double-resolved with a
real ApprovalEngine. These tests pin the fixed behavior end-to-end.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC

import pytest

from personal_ai_os.cli.api.errors import APIError, ConflictError
from tests.integration.cli.conftest import _tool_call


async def _wait_approval(client, run_id: str) -> dict:
    for _ in range(100):
        approvals = await client.list_approvals(status="pending")
        match = next((a for a in approvals if a.run_id == run_id), None)
        if match is not None:
            return {"id": match.id, "run_id": match.run_id, "tool_name": match.tool_name}
        await asyncio.sleep(0.1)
    raise AssertionError("no pending approval appeared")


@pytest.mark.asyncio
async def test_approve_once_succeeds_double_is_409(cli_client):
    """C1 — first approve works with the real engine; second is a 409."""
    script = [
        {"tool_calls": _tool_call("mail.send", {"to": "a@x.com", "subject": "s", "body": "b"})},
        {"content": "sent"},
    ]
    async with cli_client(script) as client:
        session = await client.create_session(channel="cli")
        send = await client.send_message(session.id, "email")
        approval = await _wait_approval(client, send.run_id)

        first = await client.approve(approval["id"])
        assert first.status == "approved"

        with pytest.raises(ConflictError):
            await client.approve(approval["id"])


async def _wait_terminal(client, run_id: str, expect: str = "completed") -> None:
    for _ in range(100):
        run = await client.get_run(run_id)
        if run.status in ("completed", "failed", "cancelled"):
            assert run.status == expect, f"expected {expect}, got {run.status}"
            return
        await asyncio.sleep(0.1)
    raise AssertionError("run never reached a terminal status")


@pytest.mark.asyncio
async def test_approve_then_resume_completes(cli_client):
    """C2 — resume after a separate approve no longer 500s; run completes."""
    script = [
        {"tool_calls": _tool_call("mail.send", {"to": "a@x.com", "subject": "s", "body": "b"})},
        {"content": "sent"},
    ]
    async with cli_client(script) as client:
        session = await client.create_session(channel="cli")
        send = await client.send_message(session.id, "email")
        approval = await _wait_approval(client, send.run_id)

        await client.approve(approval["id"])
        await client.resume_run(send.run_id, approval_id=approval["id"], decision="approved")
        await _wait_terminal(client, send.run_id)


@pytest.mark.asyncio
async def test_resume_with_edits_executes_edited_tool(cli_client):
    """M2 — approved_with_edits executes the tool with the edited arguments."""
    script = [
        {"tool_calls": _tool_call("mail.send", {"to": "orig@x.com", "subject": "s", "body": "b"})},
        {"content": "sent"},
    ]
    async with cli_client(script) as client:
        session = await client.create_session(channel="cli")
        send = await client.send_message(session.id, "email")
        approval = await _wait_approval(client, send.run_id)

        await client.resume_run(
            send.run_id,
            approval_id=approval["id"],
            decision="approved_with_edits",
            edited_arguments={"to": "edited@x.com", "subject": "s", "body": "b"},
        )
        await _wait_terminal(client, send.run_id)
        run = await client.get_run(send.run_id)
        calls = run.tool_calls
        assert len(calls) == 1
        # P0-003: the edited call must actually EXECUTE (broker's verify_approval
        # reads the persisted edited hash) — not just be recorded as args.
        assert calls[0].status in ("success", "completed"), calls[0].status
        assert calls[0].arguments.get("to") == "edited@x.com"


@pytest.mark.asyncio
async def test_reject_resume_completes(cli_client):
    script = [
        {"tool_calls": _tool_call("mail.send", {"to": "a@x.com", "subject": "s", "body": "b"})},
        {"content": "not sent"},
    ]
    async with cli_client(script) as client:
        session = await client.create_session(channel="cli")
        send = await client.send_message(session.id, "email")
        approval = await _wait_approval(client, send.run_id)

        await client.reject(approval["id"])
        await client.resume_run(send.run_id, approval_id=approval["id"], decision="rejected")
        await _wait_terminal(client, send.run_id)


@pytest.mark.asyncio
async def test_expired_approval_fails_closed_not_fake_success(cli_client):
    """P0-004 — a non-idempotent resolve failure (expired) must NOT be swallowed
    into a fake success; the request fails loudly and the tool never runs."""
    from datetime import datetime, timedelta

    from sqlalchemy import update

    from personal_ai_os.db.models import Approval
    from personal_ai_os.db.session import session_scope

    script = [
        {"tool_calls": _tool_call("mail.send", {"to": "a@x.com", "subject": "s", "body": "b"})},
        {"content": "sent"},
    ]
    async with cli_client(script) as client:
        session = await client.create_session(channel="cli")
        send = await client.send_message(session.id, "email")
        approval = await _wait_approval(client, send.run_id)

        # expire the pending approval in the DB
        async with session_scope() as s:
            await s.execute(
                update(Approval)
                .where(Approval.id == uuid.UUID(approval["id"]))
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )

        with pytest.raises(APIError):  # expired → fail closed, never fake success
            await client.approve(approval["id"])
        # and the run stays waiting — the tool was NOT executed
        run = await client.get_run(send.run_id)
        assert run.status == "waiting_approval"
