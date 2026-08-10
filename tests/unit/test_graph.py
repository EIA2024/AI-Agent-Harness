"""LangGraph state-machine tests: full cycles, plan flow, approval interrupt/resume."""

import uuid

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from personal_ai_os.agent_runtime.classifier import TaskClassifier
from personal_ai_os.agent_runtime.graph import build_graph
from personal_ai_os.agent_runtime.planner import Planner
from personal_ai_os.common.models import ToolResult
from tests.unit.fakes import (
    FakeContextEngine,
    FakeMemoryStore,
    FakeModelProvider,
    FakeToolBroker,
    make_tool,
    tool_call,
)


def make_initial(user_input: str) -> dict:
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


def build_compiled(script, *, tool_broker=None, memory_store=None):
    model_provider = FakeModelProvider(script)
    broker = tool_broker or FakeToolBroker(
        tools=[make_tool("search", "搜索")], results=[ToolResult.ok(text="搜索结果")]
    )
    graph = build_graph(
        context_engine=FakeContextEngine(),
        model_provider=model_provider,
        tool_broker=broker,
        memory_store=memory_store,
        classifier=TaskClassifier(),
        planner=Planner(),
    )
    compiled = graph.compile(checkpointer=InMemorySaver())
    return compiled, model_provider, broker


def cfg(run_id: str):
    return {"configurable": {"thread_id": run_id}}


async def test_full_cycle_with_tool_loop():
    """intake → build_context → decide → tool_request → execute → observe →
    decide → respond → reflect → memory_commit."""
    compiled, provider, broker = build_compiled(
        [
            {"content": None, "tool_calls": [tool_call("search", {"q": "python"})]},
            {"content": "这是最终回答", "tool_calls": None},
        ]
    )
    initial = make_initial("明天有什么安排？")
    result = await compiled.ainvoke(initial, cfg(initial["run_id"]))

    assert result["task"]["level"] == "L1"
    assert result["plan"] == []  # L1 does not plan
    assert len(result["tool_results"]) == 1
    assert result["tool_results"][0]["tool_name"] == "search"
    assert result["tool_results"][0]["success"] is True
    assert result["tool_results"][0]["arguments"] == {"q": "python"}

    # message history includes the tool-call assistant msg, tool msg, final answer
    assert [m["role"] for m in result["messages"]] == ["assistant", "tool", "assistant"]
    assert result["messages"][-1]["content"] == "这是最终回答"
    assert result["final_response"] == "这是最终回答"
    assert result["status"] == "committing_memory"
    # the model was called exactly twice (decide x2)
    assert provider.i == 2
    assert len(broker.calls) == 1


async def test_l2_task_generates_plan_before_tool():
    compiled, provider, broker = build_compiled(
        [
            {"content": None, "tool_calls": [tool_call("search", {"q": "project"})]},
            {"content": "计划完成", "tool_calls": None},
        ]
    )
    initial = make_initial("帮我整理这个项目并写学习指南")
    result = await compiled.ainvoke(initial, cfg(initial["run_id"]))

    assert result["task"]["level"] == "L2"
    assert 2 <= len(result["plan"]) <= 4
    assert result["plan"][0]["id"].startswith("step-")
    assert result["plan"][0]["status"] == "pending"
    assert result["tool_results"][0]["tool_name"] == "search"
    assert result["final_response"] == "计划完成"


async def test_simple_chat_no_tool_no_plan():
    compiled, provider, broker = build_compiled([{"content": "你好呀", "tool_calls": None}])
    initial = make_initial("你好")
    result = await compiled.ainvoke(initial, cfg(initial["run_id"]))

    assert result["task"]["level"] == "L0"
    assert result["pending_tool_call"] is None
    assert result["tool_results"] == []
    assert result["final_response"] == "你好呀"
    assert [m["role"] for m in result["messages"]] == ["assistant"]


async def test_memory_candidates_committed():
    memory_store = FakeMemoryStore()
    compiled, provider, broker = build_compiled(
        [{"content": "好的，我记住了", "tool_calls": None}],
        memory_store=memory_store,
    )
    initial = make_initial("我喜欢用Python写脚本")
    result = await compiled.ainvoke(initial, cfg(initial["run_id"]))

    assert result["memory_candidates"], "expected preference candidates from reflect"
    assert result["memory_candidates"][0]["type"] == "preference"
    assert memory_store.written, "memory_commit should call memory_store.write"
    assert memory_store.written[0].content == "我喜欢用Python写脚本"


async def test_approval_interrupt_then_resume_approved():
    broker = FakeToolBroker(
        tools=[make_tool("send_mail", "发送邮件", risk_level=3)],
        results=[ToolResult.ok(text="邮件已发送")],
        require_approval={"send_mail"},
    )
    compiled, provider, broker = build_compiled(
        [
            {"content": None, "tool_calls": [tool_call("send_mail", {"to": "a@b.c"})]},
            {"content": "邮件已发送", "tool_calls": None},
        ],
        tool_broker=broker,
    )
    initial = make_initial("帮我发一封邮件")
    thread = cfg(initial["run_id"])

    # first invocation pauses at the approval interrupt
    first = await compiled.ainvoke(initial, thread)
    assert "__interrupt__" in first
    interrupt_value = first["__interrupt__"][0].value
    assert interrupt_value["type"] == "approval"
    assert interrupt_value["tool_name"] == "send_mail"
    assert interrupt_value["approval_id"]
    assert broker.calls and broker.calls[0][0] == "send_mail"

    # resume with approval → the tool executes for real
    resumed = await compiled.ainvoke(
        Command(resume={"decision": "approved", "approved_by": str(uuid.uuid4())}), thread
    )
    assert resumed["pending_approval"] is None
    assert resumed["tool_results"][-1]["success"] is True
    assert resumed["tool_results"][-1]["tool_name"] == "send_mail"
    assert resumed["final_response"] == "邮件已发送"
    assert resumed["status"] == "committing_memory"
    assert len(broker.calls) == 2  # policy-check call + real execution


async def test_approval_rejected_routes_to_respond():
    broker = FakeToolBroker(
        tools=[make_tool("send_mail", "发送邮件", risk_level=3)],
        results=[ToolResult.ok(text="邮件已发送")],
        require_approval={"send_mail"},
    )
    compiled, provider, broker = build_compiled(
        [{"content": None, "tool_calls": [tool_call("send_mail", {"to": "a@b.c"})]}],
        tool_broker=broker,
    )
    initial = make_initial("帮我发一封邮件")
    thread = cfg(initial["run_id"])

    first = await compiled.ainvoke(initial, thread)
    assert "__interrupt__" in first

    resumed = await compiled.ainvoke(
        Command(resume={"decision": "rejected", "approved_by": str(uuid.uuid4())}), thread
    )
    assert resumed["status"] == "committing_memory"
    assert "审批未通过" in resumed["final_response"]
    assert resumed["tool_results"][-1]["success"] is False
    # only the policy-check call happened; the tool was never executed
    assert len(broker.calls) == 1


async def test_resume_with_edited_arguments():
    broker = FakeToolBroker(
        tools=[make_tool("send_mail", "发送邮件", risk_level=3)],
        results=[ToolResult.ok(text="已发送到新地址")],
        require_approval={"send_mail"},
    )
    compiled, provider, broker = build_compiled(
        [
            {"content": None, "tool_calls": [tool_call("send_mail", {"to": "old@x.com"})]},
            {"content": "已发送", "tool_calls": None},
        ],
        tool_broker=broker,
    )
    initial = make_initial("帮我发一封邮件")
    thread = cfg(initial["run_id"])

    await compiled.ainvoke(initial, thread)
    resumed = await compiled.ainvoke(
        Command(
            resume={
                "decision": "approved",
                "approved_by": str(uuid.uuid4()),
                "edited_arguments": {"to": "new@x.com"},
            }
        ),
        thread,
    )
    executed_args = broker.calls[-1][1]
    assert executed_args == {"to": "new@x.com"}
