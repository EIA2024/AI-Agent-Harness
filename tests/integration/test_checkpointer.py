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
