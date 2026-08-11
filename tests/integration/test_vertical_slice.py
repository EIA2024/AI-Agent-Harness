"""Full vertical-slice integration test — the §97 end-to-end chain.

Wires the REAL implementations together (no mocks for the core loop):

    POST message → Run created → Context built → Model decides →
    ToolBroker (validate → policy → execute) → Observe → Respond →
    Memory commit → DB persisted

Plus the security-critical paths:
    * approval flow (R3 tool → ask → user approves → verified → executes)
    * approval mismatch blocked ("approved A, executing B")
    * policy deny (credential.* blocked)
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from personal_ai_os.agent_runtime import RunRunner, build_graph
from personal_ai_os.common.models import (
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolDescriptor,
    ToolExecutionContext,
    ToolResult,
)
from personal_ai_os.context_engine import ContextEngine
from personal_ai_os.db.models import Approval, MemoryRow, Message, Run, ToolCall, User
from personal_ai_os.db.session import session_scope
from personal_ai_os.memory_engine import SQLMemoryStore
from personal_ai_os.model_gateway import DeterministicEmbedding
from personal_ai_os.policy_engine import ApprovalEngine, CredentialBroker, PolicyEngine
from personal_ai_os.scheduler import EventBus
from personal_ai_os.tool_broker import ToolBroker, ToolRegistry

# ---------------------------------------------------------------------------
# Test double: a scripted model that emits tool calls / content in sequence
# ---------------------------------------------------------------------------


class ScriptedModel:
    def __init__(self, script):
        self.script = script
        self.i = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        entry = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return ModelResponse(
            content=entry.get("content"),
            tool_calls=entry.get("tool_calls"),
            model="fake", provider="fake",
            usage=ModelUsage(input_tokens=10, output_tokens=5),
        )

    async def health_check(self) -> bool:
        return True


def _tool_call(name: str, arguments: dict) -> list[dict]:
    return [{"type": "function", "id": f"call-{uuid.uuid4().hex[:8]}",
             "function": {"name": name, "arguments": arguments}}]


# ---------------------------------------------------------------------------
# Fake R3 connector (mail) + credential tool so approval/deny paths are real
# ---------------------------------------------------------------------------


class FakeMailConnector:
    connector_name = "mail"

    async def list_tools(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(
                name="mail.send",
                namespace="mail",
                description="Send an email (external side-effect)",
                input_schema={
                    "type": "object",
                    "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
                    "required": ["to", "subject", "body"],
                },
                risk_level=3,
                side_effect=True,
                external_write=True,
            ),
            ToolDescriptor(
                name="credential.inspect",
                namespace="credential",
                description="Inspect stored credentials",
                input_schema={"type": "object", "properties": {"name": {"type": "string"}}},
                risk_level=0,
            ),
        ]

    async def execute(self, tool: str, arguments: dict, ctx: ToolExecutionContext) -> ToolResult:
        if tool == "mail.send":
            return ToolResult.ok(
                data={"sent": True, "to": arguments.get("to")},
                text=f"Email sent to {arguments.get('to')}",
            )
        if tool == "credential.inspect":
            return ToolResult.ok(data={"name": arguments.get("name")})
        return ToolResult.fail(error=f"unknown tool {tool}")


# ---------------------------------------------------------------------------
# Stack builder
# ---------------------------------------------------------------------------


async def build_stack(script):
    """Wire the real runtime + tools + policy + approval + memory and return them."""
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
    await broker.register_connector(FakeMailConnector())

    model = ScriptedModel(script)
    context_engine = ContextEngine(memory_store=memory, tool_registry=registry)

    graph = build_graph(
        context_engine=context_engine,
        model_provider=model,
        tool_broker=broker,
        memory_store=memory,
    )
    runner = RunRunner(
        graph=graph,
        model_provider=model,
        tool_broker=broker,
        memory_store=memory,
        context_engine=context_engine,
        policy_engine=policy,
        approval_engine=approval,
        event_bus=event_bus,
    )

    async with session_scope() as s:
        u = User(username=f"vslice{uuid.uuid4().hex[:8]}", api_key="vslice-key")
        s.add(u)
        await s.flush()
        owner_id = u.id

    return runner, model, broker, policy, approval, memory, owner_id


async def _make_session(owner_id) -> uuid.UUID:
    from personal_ai_os.db.models import Session

    async with session_scope() as s:
        sess = Session(owner_id=owner_id, channel="api", status="active")
        s.add(sess)
        await s.flush()
        return sess.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_flow_end_to_end():
    """L0 chat: message in → assistant reply out, run + messages persisted."""
    runner, *_ = await build_stack([{"content": "你好，我是你的助手"}])
    async with session_scope() as s:
        owner_id = (await s.execute(select(User))).scalars().first().id
    session_id = await _make_session(owner_id)

    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="你好")
    assert result["status"] == "completed"

    async with session_scope() as s:
        run = await s.get(Run, uuid.UUID(result["id"]))
        assert run.status == "completed"
        msgs = (await s.execute(select(Message).where(Message.run_id == run.id))).scalars().all()
        roles = {m.role for m in msgs}
        assert {"user", "assistant"} <= roles
        assert any("助手" in (m.content or "") for m in msgs if m.role == "assistant")


@pytest.mark.asyncio
async def test_calculator_tool_flow():
    """L1 tool: model emits calculator.evaluate → R0 auto → executed + persisted."""
    from connectors import get_builtin_connectors

    script = [
        {"tool_calls": _tool_call("calculator.evaluate", {"expression": "2 + 2"})},
        {"content": "结果是 4。"},
    ]
    runner, model, broker, *_ = await build_stack(script)
    for c in get_builtin_connectors():
        await broker.register_connector(c)

    async with session_scope() as s:
        owner_id = (await s.execute(select(User))).scalars().first().id
    session_id = await _make_session(owner_id)

    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="2+2=?")
    assert result["status"] == "completed"

    async with session_scope() as s:
        calls = (await s.execute(select(ToolCall).where(ToolCall.run_id == uuid.UUID(result["id"])))).scalars().all()
        assert len(calls) == 1
        assert calls[0].tool_name == "calculator.evaluate"
        assert calls[0].status == "success"
        assert calls[0].result["data"]["result"] == 4


@pytest.mark.asyncio
async def test_approval_flow_approved():
    """HITL: R3 tool → ask → pause → approve → verified → executes."""
    script = [
        {"tool_calls": _tool_call("mail.send", {"to": "boss@x.com", "subject": "hi", "body": "hello"})},
        {"content": "邮件已发送。"},
    ]
    runner, *_ = await build_stack(script)
    async with session_scope() as s:
        owner_id = (await s.execute(select(User))).scalars().first().id
    session_id = await _make_session(owner_id)

    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="给老板发邮件")
    assert result["status"] == "waiting_approval"

    async with session_scope() as s:
        appr = (await s.execute(select(Approval))).scalars().first()
        assert appr is not None and appr.status == "pending"
        assert appr.tool_name == "mail.send"
        approval_id = appr.id

    resumed = await runner.resume(
        run_id=result["id"], approval_id=approval_id, decision="approved"
    )
    assert resumed["status"] == "completed"

    async with session_scope() as s:
        calls = (await s.execute(select(ToolCall).where(ToolCall.run_id == uuid.UUID(resumed["id"])))).scalars().all()
        assert len(calls) == 1
        assert calls[0].status in ("completed", "success")
        assert calls[0].result["data"]["sent"] is True
        appr = await s.get(Approval, approval_id)
        assert appr.status == "approved"


@pytest.mark.asyncio
async def test_approval_rejected():
    """HITL: user rejects → tool never executes → run completes with rejection."""
    script = [
        {"tool_calls": _tool_call("mail.send", {"to": "boss@x.com", "subject": "hi", "body": "hello"})},
        {"content": "好的，不发送了。"},
    ]
    runner, *_ = await build_stack(script)
    async with session_scope() as s:
        owner_id = (await s.execute(select(User))).scalars().first().id
    session_id = await _make_session(owner_id)

    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="给老板发邮件")
    assert result["status"] == "waiting_approval"

    async with session_scope() as s:
        approval_id = (await s.execute(select(Approval))).scalars().first().id

    resumed = await runner.resume(
        run_id=result["id"], approval_id=approval_id, decision="rejected"
    )
    assert resumed["status"] == "completed"

    async with session_scope() as s:
        calls = (await s.execute(select(ToolCall).where(ToolCall.run_id == uuid.UUID(resumed["id"])))).scalars().all()
        assert all(c.status != "success" for c in calls)
        appr = await s.get(Approval, approval_id)
        assert appr.status == "rejected"


@pytest.mark.asyncio
async def test_approval_argument_mismatch_blocked():
    """Security: approving args A cannot execute args B (hash binding)."""
    runner, _, broker, _, approval, _, owner_id = await build_stack([])
    session_id = await _make_session(owner_id)

    async with session_scope() as s:
        run = Run(owner_id=owner_id, session_id=session_id, status="running", input={})
        s.add(run)
        await s.flush()
        run_id = run.id

    req = await approval.create_request(
        run_id=run_id, session_id=session_id, owner_id=owner_id,
        action_summary="send mail to A", tool_name="mail.send",
        arguments_preview={"to": "A@x.com", "subject": "s", "body": "b"},
        risk_level=3, risk_reason="external",
    )
    await approval.resolve(req.id, decision="approved", approved_by=owner_id)

    blocked = await approval.verify_approval(req.id, "mail.send", {"to": "B@x.com", "subject": "s", "body": "b"})
    assert blocked is False
    passed = await approval.verify_approval(req.id, "mail.send", {"to": "A@x.com", "subject": "s", "body": "b"})
    assert passed is True


@pytest.mark.asyncio
async def test_policy_deny():
    """credential.* denied by default policy → ToolResult failure, run completes."""
    script = [
        {"tool_calls": _tool_call("credential.inspect", {"name": "github"})},
        {"content": "无法查看凭据。"},
    ]
    runner, *_ = await build_stack(script)
    async with session_scope() as s:
        owner_id = (await s.execute(select(User))).scalars().first().id
    session_id = await _make_session(owner_id)

    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="查看凭据")
    assert result["status"] == "completed"

    async with session_scope() as s:
        calls = (await s.execute(select(ToolCall).where(ToolCall.run_id == uuid.UUID(result["id"])))).scalars().all()
        assert len(calls) == 1
        assert calls[0].status == "denied"
        assert calls[0].error["code"] == "POLICY_DENIED"


@pytest.mark.asyncio
async def test_memory_flow():
    """User preference extracted → persisted → hybrid search recalls it."""
    from personal_ai_os.common.models import MemoryQuery

    script = [{"content": "好的，我会记住你偏好 Python。"}]
    runner, _, _, _, _, memory, owner_id = await build_stack(script)
    session_id = await _make_session(owner_id)

    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="我喜欢用 Python 写算法")
    assert result["status"] == "completed"

    async with session_scope() as s:
        rows = (await s.execute(select(MemoryRow))).scalars().all()
        assert len(rows) >= 1
        assert any("Python" in r.content for r in rows)

    hits = await memory.search(MemoryQuery(owner_id=owner_id, query="Python 算法", limit=5))
    assert any("Python" in m.content for m in hits)
