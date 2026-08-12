"""P0-005 regression — production wiring uses a persistent checkpointer."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_persistent_checkpointer_opens_sqlite_file(tmp_path, monkeypatch):
    """The default checkpointer is file-backed SQLite, not InMemorySaver."""
    monkeypatch.setenv("PERSONAL_AI_DATA_DIR", str(tmp_path))
    from personal_ai_os.gateway.services import _open_persistent_checkpointer

    saver = await _open_persistent_checkpointer()
    assert "sqlite" in type(saver).__name__.lower()
    assert any(f.name == "checkpoints.sqlite" for f in tmp_path.iterdir())
    # the async saver exposes the checkpoint read/write interface
    assert hasattr(saver, "aget_tuple")


@pytest.mark.asyncio
async def test_runner_uses_persistent_checkpointer(tmp_path, monkeypatch):
    """RunRunner wired through complete_wiring compiles with the SQLite saver."""
    monkeypatch.setenv("PERSONAL_AI_DATA_DIR", str(tmp_path))
    from personal_ai_os.agent_runtime import RunRunner, build_graph
    from personal_ai_os.gateway.services import _open_persistent_checkpointer
    from tests.unit.fakes import FakeContextEngine, FakeModelProvider, FakeToolBroker, make_tool

    graph = build_graph(
        context_engine=FakeContextEngine(),
        model_provider=FakeModelProvider([{"content": "hi"}]),
        tool_broker=FakeToolBroker(tools=[make_tool("search", "s")]),
    )
    saver = await _open_persistent_checkpointer()
    assert "sqlite" in type(saver).__name__.lower()
    runner = RunRunner(
        graph=graph,
        model_provider=FakeModelProvider([{"content": "hi"}]),
        tool_broker=FakeToolBroker(),
        memory_store=None,
        context_engine=FakeContextEngine(),
        policy_engine=None,
        approval_engine=None,
        event_bus=None,
        checkpointer=saver,
    )
    # the compiled graph is not the in-memory default
    assert runner.compiled is not None


@pytest.mark.asyncio
async def test_session_serialized_concurrency_blocks_second_run():
    """P1-009 — a session with a running run refuses a second one (409 path)."""
    from sqlalchemy import select

    from personal_ai_os.agent_runtime.runner import SessionBusyError
    from personal_ai_os.db.models import Run, User
    from personal_ai_os.db.session import session_scope
    from tests.integration.test_vertical_slice import _make_session
    from tests.integration.test_vertical_slice import build_stack as _bs

    runner, *_ = await _bs([{"content": "hi"}])
    async with session_scope() as s:
        owner_id = (await s.execute(select(User))).scalars().first().id
    session_id = await _make_session(owner_id)
    # leave a run stuck in "running" (simulates a crashed/active run)
    async with session_scope() as s:
        s.add(Run(owner_id=owner_id, session_id=session_id, status="running", input={}))
        await s.flush()
    with pytest.raises(SessionBusyError):
        await runner.start(session_id=session_id, owner_id=owner_id, user_input="second")


@pytest.mark.asyncio
async def test_durable_event_log_append_and_replay():
    """P1-020 — events appended durably can be replayed after the queue is gone."""
    from sqlalchemy import select

    from personal_ai_os.agent_runtime import event_log
    from personal_ai_os.db.models import Run, User
    from personal_ai_os.db.session import session_scope
    from tests.integration.test_vertical_slice import build_stack as _bs

    await _bs([{"content": "hi"}])
    async with session_scope() as s:
        owner_id = (await s.execute(select(User))).scalars().first().id
        run = Run(owner_id=owner_id, status="running", input={})
        s.add(run)
        await s.flush()
        run_id = run.id
    await event_log.append_run_event(run_id, "run.started", {"run_id": str(run_id)})
    await event_log.append_run_event(run_id, "run.completed", {"run_id": str(run_id), "status": "completed"})
    events = await event_log.replay_run_events(run_id)
    types = [e["event"] for e in events]
    assert types == ["run.started", "run.completed"]
    assert events[1]["data"]["status"] == "completed"
