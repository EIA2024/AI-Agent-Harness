"""RunRunner tests: full run persistence, approval resume, cancel, failure."""

import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from personal_ai_os.agent_runtime.classifier import TaskClassifier
from personal_ai_os.agent_runtime.graph import build_graph
from personal_ai_os.agent_runtime.planner import Planner
from personal_ai_os.agent_runtime.runner import RunRunner
from personal_ai_os.common.models import EventTypes, ModelError, ToolResult
from personal_ai_os.db import session as db_session
from personal_ai_os.db.models import Approval, Message, Run, RunStep, ToolCall
from personal_ai_os.policy_engine.approval import ApprovalEngine
from personal_ai_os.scheduler.event_bus import EventBus
from tests.unit.fakes import (
    FakeApprovalEngine,
    FakeContextEngine,
    FakeModelProvider,
    FakePolicyEngine,
    FakeToolBroker,
    make_tool,
    tool_call,
)


def make_runner(*, script, tool_broker=None, event_bus=None, approval_engine=None):
    provider = FakeModelProvider(script)
    broker = tool_broker or FakeToolBroker(
        tools=[make_tool("search", "搜索")],
        results=[ToolResult.ok(text="搜索结果")],
    )
    context_engine = FakeContextEngine()
    graph = build_graph(
        context_engine=context_engine,
        model_provider=provider,
        tool_broker=broker,
        classifier=TaskClassifier(),
        planner=Planner(),
    )
    runner = RunRunner(
        graph=graph,
        model_provider=provider,
        tool_broker=broker,
        memory_store=None,
        context_engine=context_engine,
        policy_engine=FakePolicyEngine(),
        approval_engine=approval_engine or ApprovalEngine(),
        event_bus=event_bus,
        checkpointer=InMemorySaver(),
    )
    return runner, provider, broker


def make_approval_broker():
    return FakeToolBroker(
        tools=[make_tool("send_mail", "发送邮件", risk_level=3)],
        results=[ToolResult.ok(text="邮件已发送")],
        require_approval={"send_mail"},
    )


# ---------------------------------------------------------------------------
# start — full run with DB persistence
# ---------------------------------------------------------------------------


async def test_start_completes_run_and_persists_everything(seeded_db):
    owner_id, session_id = seeded_db
    event_bus = EventBus()
    completed = []

    async def on_completed(ev):
        completed.append(ev.type)

    event_bus.subscribe(EventTypes.RUN_COMPLETED, on_completed)

    runner, provider, broker = make_runner(
        script=[
            {"content": None, "tool_calls": [tool_call("search", {"q": "python"})]},
            {"content": "好的，这是搜索结果", "tool_calls": None},
        ],
        event_bus=event_bus,
    )
    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="明天有什么安排？")

    assert result["status"] == "completed"
    assert result["state"]["final_response"] == "好的，这是搜索结果"
    assert result["model_usage"]

    run_id = uuid.UUID(result["id"])
    async with db_session.session_scope() as s:
        run = await s.get(Run, run_id)
        assert run is not None
        assert run.status == "completed"
        assert run.input["user_input"] == "明天有什么安排？"
        assert run.completed_at is not None
        assert run.cost["currency"] == "usd"

        steps = (await s.execute(select(RunStep).where(RunStep.run_id == run_id))).scalars().all()
        assert len(steps) >= 9
        step_types = [st.step_type for st in steps]
        assert "intake" in step_types and "build_context" in step_types and "decide" in step_types
        assert "tool_request" in step_types and "observe" in step_types and "respond" in step_types

        messages = (await s.execute(select(Message).where(Message.run_id == run_id))).scalars().all()
        roles = [m.role for m in messages]
        assert "user" in roles  # the input message
        assert "assistant" in roles  # the final response
        assert "tool" in roles

        tool_calls = (await s.execute(select(ToolCall).where(ToolCall.run_id == run_id))).scalars().all()
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_name == "search"
        assert tool_calls[0].status == "success"

    assert completed == [EventTypes.RUN_COMPLETED]
    assert provider.i == 2  # decide called twice (tool + final answer)


async def test_start_simple_chat_no_tools(seeded_db):
    owner_id, session_id = seeded_db
    runner, provider, broker = make_runner(
        script=[{"content": "你好呀", "tool_calls": None}]
    )
    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="你好")
    assert result["status"] == "completed"
    assert result["state"]["final_response"] == "你好呀"
    run_id = uuid.UUID(result["id"])
    async with db_session.session_scope() as s:
        tool_calls = (await s.execute(select(ToolCall).where(ToolCall.run_id == run_id))).scalars().all()
        assert tool_calls == []


async def test_get_run_raises_for_missing_run():
    runner, provider, broker = make_runner(script=[])
    with pytest.raises(KeyError):
        await runner.get_run(uuid.uuid4())


# ---------------------------------------------------------------------------
# approval resume flow
# ---------------------------------------------------------------------------


async def test_approval_resume_flow(seeded_db):
    owner_id, session_id = seeded_db
    event_bus = EventBus()
    created = []

    async def on_approval(ev):
        created.append(ev.type)

    event_bus.subscribe(EventTypes.APPROVAL_CREATED, on_approval)

    broker = make_approval_broker()
    runner, provider, broker = make_runner(
        script=[
            {"content": None, "tool_calls": [tool_call("send_mail", {"to": "a@b.c"})]},
            {"content": "邮件已发送", "tool_calls": None},
        ],
        tool_broker=broker,
        event_bus=event_bus,
    )
    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="帮我发一封邮件")

    assert result["status"] == "waiting_approval"
    assert result["state"]["pending_approval"]["tool_name"] == "send_mail"
    assert result["state"]["pending_approval"]["tool_call_id"]
    assert result["state"]["pending_approval"]["arguments_preview"] == {"to": "a@b.c"}
    approval_id = result["state"]["pending_approval"]["approval_id"]
    assert created == [EventTypes.APPROVAL_CREATED]
    assert len(broker.calls) == 1  # only the policy check so far

    async with db_session.session_scope() as s:
        approval = await s.get(Approval, uuid.UUID(approval_id))
        assert approval is not None
        assert approval.status == "pending"
        assert approval.risk_level == 2

    # resume with approval
    resumed = await runner.resume(result["id"], approval_id=approval_id, decision="approved")
    assert resumed["status"] == "completed"
    assert resumed["state"]["final_response"] == "邮件已发送"

    async with db_session.session_scope() as s:
        approval = await s.get(Approval, uuid.UUID(approval_id))
        assert approval.status == "approved"
        tool_calls = (
            await s.execute(
                select(ToolCall).where(ToolCall.run_id == uuid.UUID(result["id"]))
            )
        ).scalars().all()
        assert len(tool_calls) == 1
        assert tool_calls[0].status == "success"
        assert str(tool_calls[0].approval_id) == approval_id

    assert len(broker.calls) == 2  # policy check + real execution


async def test_approval_resume_streams_in_background(seeded_db):
    from personal_ai_os.agent_runtime import event_log

    owner_id, session_id = seeded_db
    broker = make_approval_broker()
    runner, _, _ = make_runner(
        script=[
            {"content": None, "tool_calls": [tool_call("send_mail", {"to": "a@b.c"})]},
            {"content": "邮件已发送", "tool_calls": None},
        ],
        tool_broker=broker,
    )
    paused = await runner.start(
        session_id=session_id, owner_id=owner_id, user_input="帮我发一封邮件"
    )
    approval_id = paused["state"]["pending_approval"]["approval_id"]

    resumed = await runner.resume_streaming(
        paused["id"], approval_id=approval_id, decision="approved"
    )
    assert resumed["status"] == "running"
    task = runner._bg_tasks[paused["id"]]
    await task

    final = await runner.get_run(paused["id"])
    assert final["status"] == "completed"
    replay = await event_log.replay_run_events(paused["id"])
    assert replay[-1]["event"] == "run.completed"
    assert [item["seq"] for item in replay] == list(range(1, len(replay) + 1))

    assert len(broker.calls) == 2  # policy check + real execution


async def test_resume_rejected(seeded_db):
    owner_id, session_id = seeded_db
    broker = make_approval_broker()
    runner, provider, broker = make_runner(
        script=[{"content": None, "tool_calls": [tool_call("send_mail", {"to": "a@b.c"})]}],
        tool_broker=broker,
    )
    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="帮我发一封邮件")
    approval_id = result["state"]["pending_approval"]["approval_id"]

    resumed = await runner.resume(result["id"], approval_id=approval_id, decision="rejected")
    assert resumed["status"] == "completed"
    assert "审批未通过" in resumed["state"]["final_response"]
    # the tool was never executed — only the policy-check call happened
    assert len(broker.calls) == 1

    async with db_session.session_scope() as s:
        approval = await s.get(Approval, uuid.UUID(approval_id))
        assert approval.status == "rejected"


async def test_resume_uses_approval_engine_when_injected(seeded_db):
    owner_id, session_id = seeded_db
    broker = make_approval_broker()
    approval_engine = FakeApprovalEngine()
    runner, provider, broker = make_runner(
        script=[
            {"content": None, "tool_calls": [tool_call("send_mail", {"to": "a@b.c"})]},
            {"content": "ok", "tool_calls": None},
        ],
        tool_broker=broker,
        approval_engine=approval_engine,
    )
    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="帮我发一封邮件")
    approval_id = result["state"]["pending_approval"]["approval_id"]

    await runner.resume(result["id"], approval_id=approval_id, decision="approved")
    assert approval_engine.resolved, "approval_engine.resolve should be called"
    assert approval_engine.resolved[0][1] == "approved"


async def test_resume_waiting_approval_requires_approval_id_without_mutation(seeded_db):
    owner_id, session_id = seeded_db
    broker = make_approval_broker()
    runner, _, _ = make_runner(
        script=[
            {"content": None, "tool_calls": [tool_call("send_mail", {"to": "a@b.c"})]},
            {"content": "邮件已发送", "tool_calls": None},
        ],
        tool_broker=broker,
    )
    paused = await runner.start(
        session_id=session_id, owner_id=owner_id, user_input="帮我发一封邮件"
    )
    approval_id = paused["state"]["pending_approval"]["approval_id"]
    calls_before_resume = len(broker.calls)

    with pytest.raises(ValueError, match="approval_id"):
        await runner.resume(paused["id"], decision="approved")

    current = await runner.get_run(paused["id"])
    assert current["status"] == "waiting_approval"
    assert current["state"] == paused["state"]
    async with db_session.session_scope() as session:
        approval = await session.get(Approval, uuid.UUID(approval_id))
        assert approval.status == "pending"
    assert len(broker.calls) == calls_before_resume


async def test_resume_non_waiting_run_raises(seeded_db):
    owner_id, session_id = seeded_db
    runner, provider, broker = make_runner(script=[{"content": "hi", "tool_calls": None}])
    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="你好")
    assert result["status"] == "completed"
    with pytest.raises(ValueError):
        await runner.resume(result["id"], decision="approved")


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


async def test_cancel_rejects_pending_approval(seeded_db):
    owner_id, session_id = seeded_db
    broker = make_approval_broker()
    runner, provider, broker = make_runner(
        script=[{"content": None, "tool_calls": [tool_call("send_mail", {"to": "a@b.c"})]}],
        tool_broker=broker,
    )
    result = await runner.start(session_id=session_id, owner_id=owner_id, user_input="帮我发一封邮件")
    assert result["status"] == "waiting_approval"
    approval_id = result["state"]["pending_approval"]["approval_id"]

    await runner.cancel(result["id"])
    got = await runner.get_run(result["id"])
    assert got["status"] == "cancelled"

    async with db_session.session_scope() as s:
        approval = await s.get(Approval, uuid.UUID(approval_id))
        assert approval.status == "rejected"


# ---------------------------------------------------------------------------
# failure path
# ---------------------------------------------------------------------------


async def test_run_failed_classified(seeded_db):
    owner_id, session_id = seeded_db

    class FailingProvider(FakeModelProvider):
        async def complete(self, request):
            raise ModelError("model exploded")

    broker = FakeToolBroker(tools=[make_tool("search")], results=[ToolResult.ok(text="x")])
    context_engine = FakeContextEngine()
    graph = build_graph(
        context_engine=context_engine,
        model_provider=FailingProvider(),
        tool_broker=broker,
        classifier=TaskClassifier(),
        planner=Planner(),
    )
    runner = RunRunner(
        graph=graph,
        model_provider=FailingProvider(),
        tool_broker=broker,
        memory_store=None,
        context_engine=context_engine,
        policy_engine=FakePolicyEngine(),
        approval_engine=None,
        event_bus=None,
        checkpointer=InMemorySaver(),
    )
    with pytest.raises(ModelError):
        await runner.start(session_id=session_id, owner_id=owner_id, user_input="你好")

    # find the run by querying the most recent failed run
    async with db_session.session_scope() as s:
        runs = (await s.execute(select(Run).order_by(Run.created_at.desc()))).scalars().all()
        assert runs, "expected a Run row"
        failed = runs[0]
        assert failed.status == "failed"
        assert failed.error["code"] == "MODEL_ERROR"
        assert "model exploded" in failed.error["message"]
