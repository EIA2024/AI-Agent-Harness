"""P0-001 regression — after a tool call, the next decide must see the tool result.

The pre-fix graph loop is ``observe → decide`` (NOT through ``build_context``),
so `decide` reused the first iteration's cached context forever — the model
never saw any tool result and repeated the same tool call until MAX_TOOL_CALLS
stopped it. This test drives the REAL ContextEngine + graph and asserts the
model's second request contains a ``role: "tool"`` message.
"""

from __future__ import annotations

import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from personal_ai_os.agent_runtime.classifier import TaskClassifier
from personal_ai_os.agent_runtime.graph import build_graph
from personal_ai_os.agent_runtime.planner import Planner
from personal_ai_os.common.models import (
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolDescriptor,
    ToolExecutionContext,
    ToolResult,
)
from personal_ai_os.context_engine import ContextEngine
from personal_ai_os.memory_engine import SQLMemoryStore
from personal_ai_os.model_gateway import DeterministicEmbedding
from personal_ai_os.policy_engine import ApprovalEngine, CredentialBroker, PolicyEngine
from personal_ai_os.scheduler import EventBus
from personal_ai_os.tool_broker import ToolBroker, ToolRegistry


class RecordingModel:
    """Records every ModelRequest so tests can inspect what the model actually saw."""

    def __init__(self, script: list[dict]) -> None:
        self.script = script
        self.i = 0
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        entry = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return ModelResponse(
            content=entry.get("content"),
            tool_calls=entry.get("tool_calls"),
            model="fake",
            provider="fake",
            usage=ModelUsage(input_tokens=5, output_tokens=5),
        )

    async def health_check(self) -> bool:
        return True


class FakeListConnector:
    connector_name = "fs"

    async def list_tools(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(
                name="fs.list",
                namespace="fs",
                description="List files",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                risk_level=1,
            )
        ]

    async def execute(self, tool: str, arguments: dict, ctx: ToolExecutionContext) -> ToolResult:
        return ToolResult.ok(
            data={"entries": [{"name": "a.py"}, {"name": "b.py"}]},
            text="2 entries (e.g. a.py, b.py)",
        )


def _tool_call(name: str, arguments: dict) -> list[dict]:
    return [
        {
            "type": "function",
            "id": f"call-{uuid.uuid4().hex[:8]}",
            "function": {"name": name, "arguments": arguments},
        }
    ]


async def _compile(script: list[dict]):
    event_bus = EventBus()
    policy = PolicyEngine()
    credentials = CredentialBroker(source={})
    approval = ApprovalEngine()
    memory = SQLMemoryStore(embedding_provider=DeterministicEmbedding(), event_bus=event_bus)

    registry = ToolRegistry()
    broker = ToolBroker(
        registry=registry,
        policy_engine=policy,
        credential_broker=credentials,
        approval_engine=approval,
        event_bus=event_bus,
    )
    await broker.register_connector(FakeListConnector())

    model = RecordingModel(script)
    context_engine = ContextEngine(memory_store=memory, tool_registry=registry)
    graph = build_graph(
        context_engine=context_engine,
        model_provider=model,
        tool_broker=broker,
        memory_store=memory,
        classifier=TaskClassifier(),
        planner=Planner(),
    )
    return graph.compile(checkpointer=InMemorySaver()), model, broker


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


@pytest.mark.asyncio
async def test_second_decide_sees_tool_result():
    """The model's second request must contain the tool result message."""
    compiled, model, _broker = await _compile(
        [
            {"content": None, "tool_calls": _tool_call("fs.list", {"path": "."})},
            {"content": "done", "tool_calls": None},
        ]
    )
    initial = _initial("列一下文件")
    result = await compiled.ainvoke(initial, {"configurable": {"thread_id": initial["run_id"]}})

    assert len(model.requests) == 2, "expected exactly two decide calls"
    second = model.requests[1].messages
    roles = [m.get("role") for m in second]
    assert "tool" in roles, f"second decide reused stale context; roles={roles}"
    tool_msg = next(m for m in second if m.get("role") == "tool")
    assert "a.py" in tool_msg.get("content", "")  # real file names, not just a count
    assert result["status"] == "committing_memory"
    assert len(result["tool_results"]) == 1  # the tool actually executed


@pytest.mark.asyncio
async def test_model_does_not_repeat_same_call_after_success():
    """Once the model sees a successful result it should not re-issue the same call."""
    compiled, model, _broker = await _compile(
        [
            {"content": None, "tool_calls": _tool_call("fs.list", {"path": "."})},
            {"content": "done", "tool_calls": None},
        ]
    )
    initial = _initial("列一下文件")
    result = await compiled.ainvoke(initial, {"configurable": {"thread_id": initial["run_id"]}})
    # The model was asked exactly twice (decide → observe → decide) and made
    # exactly one tool call; the freshness fix is what lets it see the result
    # and conclude instead of looping.
    assert len(model.requests) == 2
    assert len(result["tool_results"]) == 1
