"""Session conversation memory: a new run in an existing session must see the
prior user/assistant turns (blueprint §6 session context)."""

from __future__ import annotations

import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from personal_ai_os.agent_runtime.classifier import TaskClassifier
from personal_ai_os.agent_runtime.graph import build_graph
from personal_ai_os.agent_runtime.planner import Planner
from personal_ai_os.agent_runtime.runner import RunRunner
from personal_ai_os.context_engine import ContextEngine
from personal_ai_os.db.models import Message, Session, User
from personal_ai_os.db.session import session_scope
from personal_ai_os.scheduler.event_bus import EventBus
from tests.unit.fakes import FakeApprovalEngine, FakeModelProvider, FakePolicyEngine, FakeToolBroker


def make_runner(script):
    provider = FakeModelProvider(script)
    broker = FakeToolBroker(tools=[], results=[])
    ctx = ContextEngine(memory_store=None, tool_registry=None)
    graph = build_graph(
        context_engine=ctx,
        model_provider=provider,
        tool_broker=broker,
        classifier=TaskClassifier(),
        planner=Planner(),
    )
    runner = RunRunner(
        graph=graph, model_provider=provider, tool_broker=broker,
        memory_store=None, context_engine=ctx, policy_engine=FakePolicyEngine(),
        approval_engine=FakeApprovalEngine(), event_bus=EventBus(),
        checkpointer=InMemorySaver(),
    )
    return runner, provider


async def _make_session(owner_id) -> uuid.UUID:
    async with session_scope() as s:
        sess = Session(owner_id=owner_id, channel="api", status="active")
        s.add(sess)
        await s.flush()
        return sess.id


@pytest.mark.asyncio
async def test_second_run_sees_first_exchange():
    async with session_scope() as s:
        u = User(username=f"mem{uuid.uuid4().hex[:8]}", api_key="k")
        s.add(u)
        await s.flush()
        owner_id = u.id
    session_id = await _make_session(owner_id)

    runner1, provider1 = make_runner([{"content": "你好！我是第一次回答。"}])
    r1 = await runner1.start(session_id=session_id, owner_id=owner_id, user_input="第一条消息")
    assert r1["status"] == "completed"

    runner2, provider2 = make_runner([{"content": "我记得上一轮。"}])
    r2 = await runner2.start(session_id=session_id, owner_id=owner_id, user_input="你还记得我之前说了什么吗？")
    assert r2["status"] == "completed"

    # The second run's model call must include the first assistant reply + both user turns.
    assert provider2.calls, "second run made no model call"
    sent = provider2.calls[0].messages
    all_content = " ".join(str(m.get("content") or "") for m in sent)
    assert "第一条消息" in all_content, f"prior user turn missing: {all_content}"
    assert "第一次回答" in all_content, f"prior assistant reply missing: {all_content}"

    # DB: the first run's messages are persisted exactly once (no history dup).
    async with session_scope() as s:
        first_msgs = (await s.execute(
            select(Message).where(Message.run_id == uuid.UUID(r1["id"]))
        )).scalars().all()
        assert len(first_msgs) == 2  # 1 user + 1 assistant
