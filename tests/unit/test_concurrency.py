"""Concurrency safety: two runs executing in the same process must not
cross-contaminate state, messages, or tool calls."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from personal_ai_os.agent_runtime import RunRunner, build_graph
from personal_ai_os.common.models import ModelRequest, ModelResponse, ModelUsage
from personal_ai_os.context_engine import ContextEngine
from personal_ai_os.db.models import Message, Run, User
from personal_ai_os.db.session import session_scope
from personal_ai_os.memory_engine import SQLMemoryStore
from personal_ai_os.model_gateway import DeterministicEmbedding
from personal_ai_os.policy_engine import ApprovalEngine, CredentialBroker, PolicyEngine
from personal_ai_os.scheduler import EventBus
from personal_ai_os.tool_broker import ToolBroker, ToolRegistry


class DelayedModel:
    """Replies with the user's own text (after a short sleep to widen the race
    window), so each concurrent run's reply is deterministic and independent of
    which run's model call happens to land first."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        await asyncio.sleep(0.05)  # widen interleaving
        user_text = ""
        for msg in request.messages or []:
            if msg.get("role") == "user":
                user_text = str(msg.get("content", ""))
        return ModelResponse(
            content=f"echo:{user_text}",
            tool_calls=None, model="fake", provider="fake",
            usage=ModelUsage(input_tokens=1, output_tokens=1),
        )

    async def health_check(self) -> bool:
        return True


async def _make_runner() -> tuple[RunRunner, DelayedModel]:
    from connectors import get_builtin_connectors

    event_bus = EventBus()
    policy = PolicyEngine()
    broker = ToolBroker(
        registry=ToolRegistry(), policy_engine=policy,
        credential_broker=CredentialBroker(source={}), approval_engine=ApprovalEngine(),
        event_bus=event_bus,
    )
    for c in get_builtin_connectors():
        await broker.register_connector(c)

    model = DelayedModel()
    memory = SQLMemoryStore(embedding_provider=DeterministicEmbedding(), event_bus=event_bus)
    ctx = ContextEngine(memory_store=memory, tool_registry=broker._registry)
    graph = build_graph(context_engine=ctx, model_provider=model, tool_broker=broker, memory_store=memory)
    runner = RunRunner(
        graph=graph, model_provider=model, tool_broker=broker, memory_store=memory,
        context_engine=ctx, policy_engine=policy, approval_engine=ApprovalEngine(), event_bus=event_bus,
    )
    return runner, model


@pytest.mark.asyncio
async def test_concurrent_runs_do_not_cross_contaminate(tmp_path):
    # SQLite in-memory uses a single shared connection (StaticPool), so it
    # cannot serve concurrent transactions. Use a file-backed SQLite DB here —
    # production runs on PostgreSQL with a multi-connection pool, which is the
    # scenario this test exercises.
    from personal_ai_os.db import session as db_session

    db_path = tmp_path / "concurrency.db"
    await db_session.dispose_engine()
    db_session.configure(f"sqlite+aiosqlite:///{db_path}")
    await db_session.reset_database()

    runner, model = await _make_runner()

    async with session_scope() as s:
        u1 = User(username=f"c1{uuid.uuid4().hex[:8]}", api_key="k1")
        s.add(u1)
        await s.flush()
        u2 = User(username=f"c2{uuid.uuid4().hex[:8]}", api_key="k2")
        s.add(u2)
        await s.flush()
        oid1, oid2 = u1.id, u2.id
        from personal_ai_os.db.models import Session
        s1 = Session(owner_id=oid1, channel="api", status="active")
        s.add(s1)
        await s.flush()
        s2 = Session(owner_id=oid2, channel="api", status="active")
        s.add(s2)
        await s.flush()
        sid1, sid2 = s1.id, s2.id

    async def run_one(owner_id, session_id, text):
        return await runner.start(session_id=session_id, owner_id=owner_id, user_input=text)

    results = await asyncio.gather(
        run_one(oid1, sid1, "task one"),
        run_one(oid2, sid2, "task two"),
    )
    assert results[0]["status"] == "completed"
    assert results[1]["status"] == "completed"

    # distinct final responses
    async with session_scope() as s:
        r1 = await s.get(Run, uuid.UUID(results[0]["id"]))
        r2 = await s.get(Run, uuid.UUID(results[1]["id"]))
        assert r1.state["final_response"] == "echo:task one"
        assert r2.state["final_response"] == "echo:task two"
        # each run's messages are only its own
        for run_id, text in ((r1.id, "task one"), (r2.id, "task two")):
            msgs = (await s.execute(select(Message).where(Message.run_id == run_id))).scalars().all()
            assert any(text in (m.content or "") for m in msgs)

    await db_session.dispose_engine()
