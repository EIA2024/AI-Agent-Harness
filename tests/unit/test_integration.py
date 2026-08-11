"""End-to-end integration: real ContextEngine + classifier + planner inside the
runner, with fake model / tool broker / memory store only. Verifies the whole
Runtime & Context stack composes correctly.
"""

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from personal_ai_os.agent_runtime.classifier import TaskClassifier
from personal_ai_os.agent_runtime.graph import build_graph
from personal_ai_os.agent_runtime.planner import Planner
from personal_ai_os.agent_runtime.runner import RunRunner
from personal_ai_os.common.models import ToolResult
from personal_ai_os.context_engine.budget import ContextBudget
from personal_ai_os.context_engine.engine import ContextEngine
from personal_ai_os.db import session as db_session
from personal_ai_os.db.models import Run
from tests.unit.fakes import (
    FakeMemoryStore,
    FakeModelProvider,
    FakePolicyEngine,
    FakeToolBroker,
    make_memory,
    make_tool,
    tool_call,
)


async def test_end_to_end_with_real_context_engine(seeded_db):
    owner_id, session_id = seeded_db
    memory_store = FakeMemoryStore(
        memories=[
            make_memory("用户是数据工程师，偏好中文", scope="profile", type_="profile"),
            make_memory("本周在迁移数据分析平台", scope="project", score=0.9),
        ]
    )
    broker = FakeToolBroker(
        tools=[make_tool("search", "搜索"), make_tool("calendar.list", "查询日程")],
        results=[ToolResult.ok(text="找到 3 篇相关文档")],
    )
    provider = FakeModelProvider(
        [
            {"content": None, "tool_calls": [tool_call("search", {"q": "python 异步"})]},
            {"content": "根据搜索结果，推荐用 asyncio。", "tool_calls": None},
        ]
    )
    context_engine = ContextEngine(
        memory_store=memory_store,
        tool_registry=None,
        budget=ContextBudget(max_tokens=16000),
    )
    graph = build_graph(
        context_engine=context_engine,
        model_provider=provider,
        tool_broker=broker,
        memory_store=memory_store,
        classifier=TaskClassifier(),
        planner=Planner(),
    )
    runner = RunRunner(
        graph=graph,
        model_provider=provider,
        tool_broker=broker,
        memory_store=memory_store,
        context_engine=context_engine,
        policy_engine=FakePolicyEngine(),
        approval_engine=None,
        event_bus=None,
        checkpointer=InMemorySaver(),
    )

    result = await runner.start(
        session_id=session_id,
        owner_id=owner_id,
        user_input="帮我研究 python 异步的最佳实践",
    )

    assert result["status"] == "completed"
    state = result["state"]
    assert state["task"]["level"] == "L2"  # 研究 → 多步骤
    assert len(state["plan"]) >= 2
    assert state["tool_results"][0]["tool_name"] == "search"
    assert state["final_response"] == "根据搜索结果，推荐用 asyncio。"
    # the real context engine produced system identity text for the model
    assert provider.calls and provider.calls[0].messages[0]["role"] == "system"

    async with db_session.session_scope() as s:
        runs = (await s.execute(select(Run))).scalars().all()
        assert len(runs) == 1
        assert runs[0].status == "completed"
        assert runs[0].state["final_response"]
