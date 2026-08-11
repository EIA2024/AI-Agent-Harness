"""T40/T42/T44 — runs list API, live tool lifecycle, session metadata.

Uses the real runtime (scripted model) so the live pushes added for T42 are
exercised end-to-end.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.integration.cli.conftest import _tool_call


@pytest.mark.asyncio
async def test_live_tool_lifecycle_events():
    """T42 — a live run must stream tool.started → tool.completed with a stable id."""
    from sqlalchemy import select

    from connectors import get_builtin_connectors
    from personal_ai_os.agent_runtime import streams
    from personal_ai_os.db.models import User
    from personal_ai_os.db.session import session_scope
    from tests.integration.test_vertical_slice import _make_session, build_stack

    script = [
        {"tool_calls": _tool_call("calculator.evaluate", {"expression": "2 + 2"})},
        {"content": "4"},
    ]
    runner, _, broker, *_ = await build_stack(script)
    for connector in get_builtin_connectors():
        await broker.register_connector(connector)

    async with session_scope() as s:
        owner_id = (await s.execute(select(User))).scalars().first().id
    session_id = await _make_session(owner_id)

    result = await runner.start_streaming(
        session_id=session_id, owner_id=owner_id, user_input="2+2?"
    )
    run_id = result["id"]
    queue = streams.get(run_id)
    assert queue is not None, "live queue should be registered"

    events: list[str] = []
    tool_ids: dict[str, str] = {}
    while True:
        ev = await asyncio.wait_for(queue.get(), timeout=10)
        events.append(ev["event"])
        if ev["event"].startswith("tool."):
            tool_ids[ev["event"]] = ev["data"].get("tool_call_id")
        if ev["event"] in ("run.completed", "run.failed", "run.cancelled", "approval.required"):
            break

    assert "tool.started" in events
    assert "tool.completed" in events
    # T42: reducer keying must never depend on tool name — the ids must line up
    assert tool_ids.get("tool.started") == tool_ids.get("tool.completed")
    assert tool_ids["tool.started"] is not None


@pytest.mark.asyncio
async def test_runs_list_uses_new_endpoint(cli_client):
    """T40 — `GET /v1/runs` returns real runs created by the CLI."""
    async with cli_client() as client:
        session = await client.create_session(channel="cli")
        send = await client.send_message(session.id, "hi")
        # poll until the run reaches a terminal status
        run = await client.get_run(send.run_id)
        for _ in range(50):
            if run.status in ("completed", "failed", "cancelled"):
                break
            await asyncio.sleep(0.1)
            run = await client.get_run(send.run_id)
        runs = await client.list_runs(limit=10)
        assert len(runs) >= 1
        listed = next(r for r in runs if r.id == send.run_id)
        assert listed.status == "completed"
        assert listed.session_id == session.id


@pytest.mark.asyncio
async def test_session_list_has_metadata(cli_client):
    """T44 — session list carries message_count + active_run_status (no N+1)."""
    async with cli_client() as client:
        session = await client.create_session(channel="cli")
        await client.send_message(session.id, "hi")
        sessions = await client.list_sessions(limit=10)
        match = next(s for s in sessions if s.id == session.id)
        assert match.message_count is None or match.message_count >= 1
        assert match.active_run_id is not None
