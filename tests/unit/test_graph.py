"""LangGraph state-machine tests: full cycles, plan flow, approval interrupt/resume."""

import uuid

import pytest
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


def test_tool_message_content_includes_data_preview():
    """The model must see real content, not just the count summary."""
    from personal_ai_os.agent_runtime.graph import _tool_message_content

    entry = {
        "text": "1859 entries",
        "data": {
            "path": ".",
            "entries": [
                {"name": "src", "path": "src", "is_dir": True},
                {"name": "docs", "path": "docs", "is_dir": True},
            ],
        },
    }
    content = _tool_message_content(entry)
    assert "1859 entries" in content
    assert "src" in content and "docs" in content
    # small listing fits the preview: no truncation warning
    assert "truncated" not in content


def test_tool_message_content_truncation_is_explicit_and_actionable():
    """A huge listing must warn the model it is only a sample, not the full set."""
    from personal_ai_os.agent_runtime.graph import _TRUNCATED_HINT, _tool_message_content

    entry = {
        "text": "2000 items",
        "data": {"entries": [{"name": f"file-{i}.py", "path": f"file-{i}.py", "is_dir": False} for i in range(2000)]},
    }
    content = _tool_message_content(entry)
    assert "file-0.py" in content          # sample items present
    assert "file-1999.py" not in content   # tail omitted
    assert "entries_total" in content      # model told the true count
    assert _TRUNCATED_HINT in content      # model told it's incomplete + how to narrow
    assert len(content) < 5000             # bounded


def test_tool_message_content_bounded_for_huge_data():
    from personal_ai_os.agent_runtime.graph import _tool_message_content

    entry = {"text": "2000 items", "data": {"entries": [{"x": "y" * 200} for _ in range(1000)]}}
    content = _tool_message_content(entry)
    assert len(content) < 5000
    assert "truncated" in content


def test_tool_message_content_without_data():
    from personal_ai_os.agent_runtime.graph import _tool_message_content

    assert _tool_message_content({"text": "ok"}) == "ok"
    assert _tool_message_content({"error": "boom"}) == "boom"


async def test_invalid_tool_args_are_not_executed():
    """P1-031 — bad arguments JSON must fail loudly, never execute with {}."""
    bad_tool_call = {
        "type": "function",
        "id": "call-bad",
        "function": {"name": "search", "arguments": "{bad json"},
    }
    compiled, provider, broker = build_compiled(
        [
            {"content": None, "tool_calls": [bad_tool_call]},
            {"content": "收到错误", "tool_calls": None},
        ]
    )
    initial = make_initial("查一下")
    result = await compiled.ainvoke(initial, cfg(initial["run_id"]))

    assert len(broker.calls) == 0  # the connector was NEVER invoked
    failed = [t for t in result["tool_results"] if t.get("error_code") == "TOOL_ARGUMENT_PARSE_ERROR"]
    assert failed
    # observe folded the parse error into the transcript the model will see
    assert "TOOL_ARGUMENT_PARSE_ERROR" in str(result["messages"])


async def test_missing_owner_id_fails_fast():
    """P1-032 — a run without identity must fail before any tool executes."""
    compiled, _provider, _broker = build_compiled([{"content": "hi"}])
    initial = make_initial("hi")
    del initial["owner_id"]
    with pytest.raises(ValueError, match="owner_id"):
        await compiled.ainvoke(initial, cfg(initial["run_id"]))


async def test_missing_run_id_fails_fast():
    """P1-032 — no fabricated UUIDs for a missing run id."""
    compiled, _provider, _broker = build_compiled([{"content": "hi"}])
    initial = make_initial("hi")
    del initial["run_id"]
    with pytest.raises(ValueError, match="run_id"):
        await compiled.ainvoke(initial, cfg(uuid.uuid4()))


async def test_stream_break_after_first_token_does_not_retry():
    """P1-016 — a stream that broke mid-output must not trigger a second model call."""
    from types import SimpleNamespace

    from personal_ai_os.agent_runtime import streams
    from personal_ai_os.agent_runtime.graph import ModelStreamError, _Deps, _model_call
    from personal_ai_os.common.models import ModelRequest, ModelResponse

    run_id = str(uuid.uuid4())
    streams.register(run_id)
    try:
        class BrokenStream:
            def __init__(self):
                self.complete_calls = 0

            async def stream(self, request):
                yield SimpleNamespace(type="text_delta", text="partial")
                raise RuntimeError("connection lost")

            async def complete(self, request):
                self.complete_calls += 1
                return ModelResponse(content="retried", tool_calls=None, model="fake", provider="fake")

        provider = BrokenStream()
        deps = _Deps(
            context_engine=None, model_provider=provider, tool_broker=None,
            memory_store=None, classifier=None, planner=None,
        )
        state = {
            "run_id": run_id, "owner_id": "o", "session_id": "s", "agent_id": None,
            "user_input": "x", "messages": [], "context_items": [], "task": {},
            "plan": [], "current_step": 0, "tool_results": [], "pending_approval": None,
            "memory_candidates": [], "skill_candidates": [], "status": "intake",
            "error": {}, "model_usage": {},
        }
        with pytest.raises(ModelStreamError):
            await _model_call(
                deps, state,
                ModelRequest(purpose="assistant", messages=[{"role": "user", "content": "x"}]),
            )
        assert provider.complete_calls == 0  # NO silent retry
    finally:
        streams.unregister(run_id)


async def test_stream_break_before_first_token_retries_non_streaming():
    """P1-016 — failing before any delta may safely fall back to complete()."""

    from personal_ai_os.agent_runtime import streams
    from personal_ai_os.agent_runtime.graph import _Deps, _model_call
    from personal_ai_os.common.models import ModelRequest, ModelResponse

    run_id = str(uuid.uuid4())
    streams.register(run_id)
    try:
        class BrokenStream:
            def __init__(self):
                self.complete_calls = 0

            async def stream(self, request):
                if False:
                    yield None  # make this an async generator
                raise RuntimeError("connection refused")

            async def complete(self, request):
                self.complete_calls += 1
                return ModelResponse(content="ok", tool_calls=None, model="fake", provider="fake")

        provider = BrokenStream()
        deps = _Deps(
            context_engine=None, model_provider=provider, tool_broker=None,
            memory_store=None, classifier=None, planner=None,
        )
        state = {"run_id": run_id, "owner_id": "o", "session_id": "s", "agent_id": None,
                 "user_input": "x", "messages": [], "context_items": [], "task": {},
                 "plan": [], "current_step": 0, "tool_results": [], "pending_approval": None,
                 "memory_candidates": [], "skill_candidates": [], "status": "intake",
                 "error": {}, "model_usage": {}}
        response = await _model_call(
            deps, state,
            ModelRequest(purpose="assistant", messages=[{"role": "user", "content": "x"}]),
        )
        assert provider.complete_calls == 1
        assert response.content == "ok"
    finally:
        streams.unregister(run_id)
