"""Regression tests for deterministic tool-loop convergence guards."""

import uuid

from langgraph.checkpoint.memory import InMemorySaver

from personal_ai_os.agent_runtime.classifier import TaskClassifier
from personal_ai_os.agent_runtime.graph import (
    MAX_TOOL_CALLS,
    _consecutive_repeat_blocks,
    _tool_limit_count,
    build_graph,
)
from personal_ai_os.agent_runtime.planner import Planner
from personal_ai_os.common.models import ToolResult
from tests.unit.fakes import (
    FakeContextEngine,
    FakeModelProvider,
    FakeToolBroker,
    make_tool,
    tool_call,
)


def _initial(user_input: str) -> dict:
    return {
        "run_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "owner_id": str(uuid.uuid4()),
        "agent_id": None,
        "user_input": user_input,
        "messages": [],
        "context_items": [],
        "task": {},
        "plan": [],
        "current_step": 0,
        "pending_tool_call": None,
        "tool_results": [],
        "pending_approval": None,
        "memory_candidates": [],
        "skill_candidates": [],
        "status": "intake",
        "error": {},
        "model_usage": {},
    }


def _compiled(script):
    provider = FakeModelProvider(script)
    broker = FakeToolBroker(
        tools=[make_tool("search", "搜索")],
        results=[ToolResult.ok(text="搜索结果")],
    )
    graph = build_graph(
        context_engine=FakeContextEngine(),
        model_provider=provider,
        tool_broker=broker,
        classifier=TaskClassifier(),
        planner=Planner(),
    )
    return graph.compile(checkpointer=InMemorySaver()), provider, broker


def _cfg(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}


def test_repeat_blocks_do_not_consume_global_tool_budget():
    success = {
        "tool_name": "search",
        "arguments": {"q": "python"},
        "success": True,
    }
    blocked = {
        "tool_name": "search",
        "arguments": {"q": "python"},
        "success": False,
        "error_code": "TOOL_REPEAT_BLOCKED",
    }
    state = {"tool_results": [success, blocked, blocked, blocked, blocked]}

    assert _tool_limit_count(state) == 1
    assert _consecutive_repeat_blocks(state) == 4


async def test_one_repeat_block_allows_a_materially_different_recovery_call():
    first = tool_call("search", {"q": "python"}, "call-1")
    different = tool_call("search", {"q": "python docs"}, "call-2")
    compiled, provider, broker = _compiled(
        [
            {"content": None, "tool_calls": [first]},
            {"content": None, "tool_calls": [first]},
            {"content": None, "tool_calls": [different]},
            {"content": "完成", "tool_calls": None},
        ]
    )
    initial = _initial("查一下 python")

    result = await compiled.ainvoke(initial, _cfg(initial["run_id"]))

    assert len(broker.calls) == 2
    assert broker.calls[0][1] == {"q": "python"}
    assert broker.calls[1][1] == {"q": "python docs"}
    assert provider.calls[2].tools is not None
    assert result["final_response"] == "完成"
    assert result["tool_repeat_blocked_count"] == 1


async def test_two_consecutive_repeat_blocks_reclaim_tool_control_early():
    same = tool_call("search", {"q": "python"}, "call-repeat")
    compiled, provider, broker = _compiled(
        [
            {"content": None, "tool_calls": [same]},
            {"content": None, "tool_calls": [same]},
            {"content": None, "tool_calls": [same]},
            # Simulate a stubborn provider that still emits a tool call even
            # though the runtime no longer advertises tools.
            {"content": "根据已有搜索结果直接回答", "tool_calls": [same]},
        ]
    )
    initial = _initial("查一下 python")

    result = await compiled.ainvoke(initial, _cfg(initial["run_id"]))

    assert len(broker.calls) == 1
    assert provider.calls[3].tools is None
    assert result["final_response"] == "根据已有搜索结果直接回答"
    assert result["tool_loop_guard_reason"] == "repeat_blocked"
    assert result["tool_repeat_blocked_count"] == 2
    assert len(result["tool_results"]) == 3

    forced_messages = provider.calls[3].messages
    assert any(
        "不得声称工具不可用" in str(message.get("content") or "")
        for message in forced_messages
    )


async def test_repeat_block_does_not_advance_l2_plan_step():
    same = tool_call("search", {"q": "project"}, "call-plan")
    compiled, _provider, broker = _compiled(
        [
            {"content": None, "tool_calls": [same]},
            {"content": None, "tool_calls": [same]},
            {"content": "计划继续基于已有结果", "tool_calls": None},
        ]
    )
    initial = _initial("帮我整理这个项目并写学习指南")

    result = await compiled.ainvoke(initial, _cfg(initial["run_id"]))

    assert result["task"]["level"] == "L2"
    assert len(broker.calls) == 1
    # Only the connector execution advances the plan. The blocked pseudo-call
    # routes directly to observe and must not mark another plan step complete.
    assert result["current_step"] == 1
    assert result["plan"][0]["status"] == "done"
    if len(result["plan"]) > 1:
        assert result["plan"][1]["status"] == "pending"


async def test_global_limit_still_stops_five_real_tool_turns():
    calls = [tool_call("search", {"q": f"q{i}"}, f"call-{i}") for i in range(MAX_TOOL_CALLS + 1)]
    script = [{"content": None, "tool_calls": [call]} for call in calls[:MAX_TOOL_CALLS]]
    script.append(
        {
            "content": "已达到工具上限，基于已有结果回答",
            "tool_calls": [calls[-1]],
        }
    )
    compiled, provider, broker = _compiled(script)
    initial = _initial("做一个多步搜索")

    result = await compiled.ainvoke(initial, _cfg(initial["run_id"]))

    assert len(broker.calls) == MAX_TOOL_CALLS
    assert provider.calls[MAX_TOOL_CALLS].tools is None
    assert result["tool_loop_guard_reason"] == "max_tool_calls"
    assert result["final_response"] == "已达到工具上限，基于已有结果回答"
