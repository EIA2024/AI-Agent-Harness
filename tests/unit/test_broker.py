"""End-to-end ToolBroker tests with fake policy / credential / connector."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select

from personal_ai_os.common.models import (
    ApprovalRequiredError,
    PolicyDecision,
    ToolDescriptor,
    ToolExecutionContext,
    ToolResult,
)
from personal_ai_os.db.models import Run, ToolCall, User
from personal_ai_os.db.session import configure, dispose_engine, init_db, session_scope
from personal_ai_os.tool_broker.broker import ToolBroker
from personal_ai_os.tool_broker.registry import ToolRegistry

SECRET = "sk-abcdefghijklmnop1234567890"

ECHO_SCHEMA = {
    "type": "object",
    "properties": {
        "message": {"type": "string"},
        "count": {"type": "integer"},
    },
    "required": ["message"],
    "additionalProperties": False,
}


def echo_tool(*, timeout_seconds: int = 30) -> ToolDescriptor:
    return ToolDescriptor(
        name="fake.echo",
        namespace="fake",
        description="Echo the message back",
        input_schema=ECHO_SCHEMA,
        risk_level=1,
        idempotent=True,
        timeout_seconds=timeout_seconds,
        tags=["echo", "test"],
    )


def ctx(*, owner_id=None, run_id=None, idempotency_key: str | None = "ik-1") -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=run_id or uuid4(),
        owner_id=owner_id or uuid4(),
        idempotency_key=idempotency_key,
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakePolicy:
    def __init__(self, decision: str = "allow") -> None:
        self.decision = decision
        self.calls: list = []

    async def evaluate(self, tool, arguments, context) -> PolicyDecision:
        self.calls.append((tool, arguments, context))
        return PolicyDecision(decision=self.decision, risk_level=tool.risk_level, reasons=["test"])


class FakeCredential:
    def __init__(self, extra: dict | None = None) -> None:
        self.extra = extra or {}
        self.calls: list = []

    async def inject(self, tool, arguments, owner_id) -> dict:
        self.calls.append((tool, arguments, owner_id))
        out = dict(arguments)
        out.update(self.extra)
        return out


class FakeConnector:
    def __init__(self, result: ToolResult | None = None, *, sleep: float = 0.0) -> None:
        self.connector_name = "fake"
        self.result = result or ToolResult.ok(data={"echoed": True}, text="done")
        self.sleep = sleep
        self.executed: list = []

    async def list_tools(self) -> list[ToolDescriptor]:
        return [echo_tool()]

    async def execute(self, tool: str, arguments: dict, ctx: ToolExecutionContext) -> ToolResult:
        self.executed.append((tool, arguments, ctx))
        if self.sleep:
            await asyncio.sleep(self.sleep)
        return self.result


class FakeEventBus:
    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, event) -> None:
        self.events.append(event)


def make_broker(
    *,
    policy=None,
    credential=None,
    connector=None,
    event_bus=None,
    max_result_chars: int = 8000,
) -> tuple[ToolBroker, ToolRegistry, FakeConnector]:
    registry = ToolRegistry()
    connector = connector or FakeConnector()
    broker = ToolBroker(
        registry=registry,
        policy_engine=policy or FakePolicy(),
        credential_broker=credential or FakeCredential(),
        connectors=None,
        event_bus=event_bus,
        max_result_chars=max_result_chars,
    )
    return broker, registry, connector


async def register_echo(broker: ToolBroker, connector: FakeConnector) -> None:
    await broker.register_connector(connector)


# ---------------------------------------------------------------------------
# Not found / schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_tool_returns_not_found():
    broker, _registry, _connector = make_broker()
    result = await broker.execute("does.not.exist", {"message": "x"}, ctx())
    assert result.success is False
    assert result.error_code == "TOOL_NOT_FOUND"


@pytest.mark.asyncio
async def test_schema_validation_failure():
    broker, registry, connector = make_broker()
    await register_echo(broker, connector)
    result = await broker.execute("fake.echo", {"count": "not-an-int"}, ctx())
    assert result.success is False
    assert result.error_code == "SCHEMA_VALIDATION_ERROR"
    assert "Invalid arguments" in result.error
    assert connector.executed == []  # never reached the connector


@pytest.mark.asyncio
async def test_schema_validation_missing_required():
    broker, registry, connector = make_broker()
    await register_echo(broker, connector)
    result = await broker.execute("fake.echo", {}, ctx())
    assert result.success is False
    assert result.error_code == "SCHEMA_VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Policy decisions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allow_executes_successfully():
    broker, registry, connector = make_broker()
    await register_echo(broker, connector)
    c = ctx()
    result = await broker.execute("fake.echo", {"message": "hi"}, c)
    assert result.success
    assert result.data["echoed"] is True
    assert connector.executed[0][0] == "fake.echo"
    assert connector.executed[0][1] == {"message": "hi"}


@pytest.mark.asyncio
async def test_allow_passes_credential_injection():
    broker, _registry, connector = make_broker(credential=FakeCredential(extra={"api_key": "k"}))
    await register_echo(broker, connector)
    result = await broker.execute("fake.echo", {"message": "hi"}, ctx())
    assert result.success
    # connector receives the credential-injected arguments
    assert connector.executed[0][1]["api_key"] == "k"


@pytest.mark.asyncio
async def test_deny_returns_policy_denied():
    broker, registry, connector = make_broker(policy=FakePolicy("deny"))
    await register_echo(broker, connector)
    result = await broker.execute("fake.echo", {"message": "hi"}, ctx())
    assert result.success is False
    assert result.error_code == "POLICY_DENIED"
    assert connector.executed == []  # connector never called


@pytest.mark.asyncio
async def test_ask_raises_approval_required():
    broker, registry, connector = make_broker(policy=FakePolicy("ask"))
    await register_echo(broker, connector)
    c = ctx()
    with pytest.raises(ApprovalRequiredError) as exc_info:
        await broker.execute("fake.echo", {"message": "hi"}, c)
    err = exc_info.value
    assert err.tool_name == "fake.echo"
    assert err.risk_level == 1
    assert err.reason
    assert err.request_id is not None
    assert connector.executed == []


@pytest.mark.asyncio
async def test_policy_engine_exception_fails_closed():
    class ExplodingPolicy:
        async def evaluate(self, tool, arguments, context):
            raise RuntimeError("boom")

    broker, registry, connector = make_broker(policy=ExplodingPolicy())
    await register_echo(broker, connector)
    result = await broker.execute("fake.echo", {"message": "hi"}, ctx())
    assert result.success is False
    assert result.error_code == "POLICY_DENIED"


# ---------------------------------------------------------------------------
# Connector errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_connector_registered():
    broker, registry, _connector = make_broker()
    registry.register(echo_tool())  # registered without a connector
    result = await broker.execute("fake.echo", {"message": "hi"}, ctx())
    assert result.success is False
    assert result.error_code == "NO_CONNECTOR"


@pytest.mark.asyncio
async def test_connector_failure_returned():
    broker, _registry, _connector = make_broker(
        connector=FakeConnector(result=ToolResult.fail(error="backend down", error_code="BACKEND_ERROR"))
    )
    await register_echo(broker, _connector)
    result = await broker.execute("fake.echo", {"message": "hi"}, ctx())
    assert result.success is False
    assert result.error_code == "BACKEND_ERROR"


@pytest.mark.asyncio
async def test_connector_raises_is_wrapped():
    class ExplodingConnector(FakeConnector):
        async def execute(self, tool, arguments, ctx):
            raise RuntimeError("connector exploded")

    broker, _registry, connector = make_broker(connector=ExplodingConnector())
    await register_echo(broker, connector)
    result = await broker.execute("fake.echo", {"message": "hi"}, ctx())
    assert result.success is False
    assert result.error_code == "CONNECTOR_ERROR"
    assert "connector exploded" in result.error


@pytest.mark.asyncio
async def test_broker_timeout():
    broker, _registry, connector = make_broker(connector=FakeConnector(sleep=1.0))
    # timeout_seconds is 0.05 (sub-second) so the test is fast
    slow_echo = echo_tool(timeout_seconds=0.05)
    await broker.register_connector(_SlowConnector(slow_echo, connector))
    result = await broker.execute("fake.echo", {"message": "hi"}, ctx())
    assert result.success is False
    assert result.error_code == "TIMEOUT"


class _SlowConnector:
    """Connector exposing a tool whose execution sleeps longer than its timeout."""

    connector_name = "fake"

    def __init__(self, tool: ToolDescriptor, inner: FakeConnector) -> None:
        self._tool = tool
        self._inner = inner

    async def list_tools(self) -> list[ToolDescriptor]:
        return [self._tool]

    async def execute(self, tool: str, arguments: dict, ctx: ToolExecutionContext) -> ToolResult:
        return await self._inner.execute(tool, arguments, ctx)


# ---------------------------------------------------------------------------
# Sanitization / truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_result_sanitization_redacts_secrets():
    broker, _registry, _connector = make_broker(
        credential=FakeCredential(extra={"api_key": SECRET}),
        connector=FakeConnector(
            result=ToolResult.ok(
                data={"echoed": SECRET, "api_key": SECRET},
                text=f"done token={SECRET}",
            )
        ),
    )
    await register_echo(broker, _connector)
    result = await broker.execute("fake.echo", {"message": "hi"}, ctx())
    assert result.success
    assert SECRET not in result.text
    assert "[REDACTED]" in result.text
    # secret-keyed data values are redacted by key name and by value pattern
    assert result.data["api_key"] == "[REDACTED]"
    assert result.data["echoed"] == "[REDACTED]"
    # the connector still received the RAW secret
    assert _connector.executed[0][1]["api_key"] == SECRET


@pytest.mark.asyncio
async def test_result_truncation():
    long_text = "x" * 5000
    broker, _registry, connector = make_broker(
        connector=FakeConnector(result=ToolResult.ok(text=long_text)),
        max_result_chars=100,
    )
    await register_echo(broker, connector)
    result = await broker.execute("fake.echo", {"message": "hi"}, ctx())
    assert result.success
    assert result.truncated is True
    assert len(result.text) == 100
    assert result.raw_size_bytes == 5000


@pytest.mark.asyncio
async def test_short_result_not_truncated():
    broker, _registry, connector = make_broker(
        connector=FakeConnector(result=ToolResult.ok(text="short")), max_result_chars=100
    )
    await register_echo(broker, connector)
    result = await broker.execute("fake.echo", {"message": "hi"}, ctx())
    assert result.success
    assert result.truncated is False
    assert result.text == "short"


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_events_published_on_success():
    bus = FakeEventBus()
    broker, _registry, connector = make_broker(event_bus=bus)
    await register_echo(broker, connector)
    await broker.execute("fake.echo", {"message": "hi"}, ctx())
    types = [e.type for e in bus.events]
    assert "tool.requested" in types
    assert "tool.completed" in types


@pytest.mark.asyncio
async def test_denied_event_published():
    bus = FakeEventBus()
    broker, _registry, connector = make_broker(policy=FakePolicy("deny"), event_bus=bus)
    await register_echo(broker, connector)
    await broker.execute("fake.echo", {"message": "hi"}, ctx())
    types = [e.type for e in bus.events]
    assert "tool.denied" in types


@pytest.mark.asyncio
async def test_failed_event_published():
    bus = FakeEventBus()
    broker, _registry, _connector = make_broker(
        event_bus=bus,
        connector=FakeConnector(result=ToolResult.fail(error="x", error_code="BACKEND_ERROR")),
    )
    await register_echo(broker, _connector)
    await broker.execute("fake.echo", {"message": "hi"}, ctx())
    types = [e.type for e in bus.events]
    assert "tool.failed" in types


@pytest.mark.asyncio
async def test_event_bus_failure_does_not_break_execution():
    class BrokenBus:
        async def publish(self, event) -> None:
            raise RuntimeError("bus down")

    broker, _registry, connector = make_broker(event_bus=BrokenBus())
    await register_echo(broker, connector)
    result = await broker.execute("fake.echo", {"message": "hi"}, ctx())
    assert result.success  # execution unaffected by broken event bus


# ---------------------------------------------------------------------------
# ToolCall persistence
# ---------------------------------------------------------------------------


async def seed_user_and_run(owner_id, run_id) -> None:
    async with session_scope() as session:
        session.add(User(id=owner_id, username=f"user-{uuid4().hex[:8]}"))
        await session.flush()  # ensure the FK target exists before Run is inserted
        session.add(Run(id=run_id, owner_id=owner_id, status="running", input={}))
        await session.commit()


@pytest.mark.asyncio
async def test_tool_call_recorded_to_db():
    configure("sqlite+aiosqlite:///:memory:")
    await init_db()
    try:
        owner = uuid4()
        run_id = uuid4()
        await seed_user_and_run(owner, run_id)

        broker, _registry, _connector = make_broker(
            credential=FakeCredential(extra={"api_key": SECRET})
        )
        await register_echo(broker, _connector)
        c = ToolExecutionContext(
            run_id=run_id, owner_id=owner, idempotency_key="ik-rec-1"
        )
        result = await broker.execute("fake.echo", {"message": "hi"}, c)
        assert result.success

        async with session_scope() as session:
            rows = (await session.execute(select(ToolCall).where(ToolCall.run_id == run_id))).scalars().all()
            assert len(rows) == 1
            row = rows[0]
            assert row.tool_name == "fake.echo"
            assert row.status == "success"
            assert row.risk_level == 1
            assert row.idempotency_key == "ik-rec-1"
            # Credentials travel through the in-memory execution context and
            # never enter the persisted argument map.
            assert row.arguments["message"] == "hi"
            assert "api_key" not in row.arguments
            assert SECRET not in repr(row.arguments)
    finally:
        await dispose_engine()


@pytest.mark.asyncio
async def test_denied_tool_call_recorded():
    configure("sqlite+aiosqlite:///:memory:")
    await init_db()
    try:
        owner = uuid4()
        run_id = uuid4()
        await seed_user_and_run(owner, run_id)

        broker, _registry, _connector = make_broker(policy=FakePolicy("deny"))
        await register_echo(broker, _connector)
        c = ToolExecutionContext(run_id=run_id, owner_id=owner)
        result = await broker.execute("fake.echo", {"message": "hi"}, c)
        assert result.error_code == "POLICY_DENIED"

        async with session_scope() as session:
            rows = (await session.execute(select(ToolCall).where(ToolCall.run_id == run_id))).scalars().all()
            assert len(rows) == 1
            assert rows[0].status == "denied"
            assert rows[0].error["code"] == "POLICY_DENIED"
    finally:
        await dispose_engine()


# ---------------------------------------------------------------------------
# list_available_tools / test_tool / resolve_connector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_available_tools():
    broker, registry, connector = make_broker()
    registry.register(echo_tool())
    registry.register(
        ToolDescriptor(name="risky.delete", namespace="risky", description="delete", input_schema={}, risk_level=4)
    )
    available = await broker.list_available_tools(owner_id=uuid4())
    names = {t.name for t in available}
    assert "fake.echo" in names
    assert "risky.delete" in names  # risk 4 == risk_max 4, still listed

    filtered = await broker.list_available_tools(owner_id=uuid4(), query="echo")
    assert [t.name for t in filtered] == ["fake.echo"]


@pytest.mark.asyncio
async def test_validate_tool_does_not_execute_or_consult_policy():
    """P1-015 — validate_tool is a dry-run: never executes, never touches policy."""
    policy = FakePolicy("deny")  # deny would block a real call
    broker, _registry, connector = make_broker(policy=policy)
    await register_echo(broker, connector)
    result = await broker.validate_tool("fake.echo", {"message": "hi"}, owner_id=uuid4())
    assert result.success is True
    assert connector.executed == []  # the connector never ran
    assert policy.calls == []  # policy never consulted


@pytest.mark.asyncio
async def test_validate_tool_still_validates_schema():
    broker, _registry, connector = make_broker()
    await register_echo(broker, connector)
    result = await broker.validate_tool("fake.echo", {"count": "bad"}, owner_id=uuid4())
    assert result.success is False
    assert result.error_code == "SCHEMA_VALIDATION_ERROR"
    assert connector.executed == []


@pytest.mark.asyncio
async def test_resolve_connector_priority():
    reg = ToolRegistry()
    c_namespace = FakeConnector()
    c_source = FakeConnector()
    c_prefix = FakeConnector()
    broker = ToolBroker(
        registry=reg,
        policy_engine=FakePolicy(),
        credential_broker=FakeCredential(),
        connectors={"ns": c_namespace, "native": c_source, "pre": c_prefix},
    )

    tool_ns = ToolDescriptor(name="ns.a", namespace="ns", description="d", input_schema={}, source="other")
    tool_src = ToolDescriptor(name="x.b", namespace="x", description="d", input_schema={}, source="native")
    tool_pre = ToolDescriptor(name="pre.c", namespace="yyy", description="d", input_schema={}, source="mcp")

    assert broker.resolve_connector(tool_ns) is c_namespace
    assert broker.resolve_connector(tool_src) is c_source
    assert broker.resolve_connector(tool_pre) is c_prefix


@pytest.mark.asyncio
async def test_register_connector_indexes_under_namespace():
    broker, registry, connector = make_broker()
    await broker.register_connector(connector)
    # the registered tool resolves to the connector via namespace, prefix, and connector_name
    assert broker.resolve_connector(echo_tool()) is connector
    assert registry.get("fake.echo") is not None


async def test_capability_service_filters_owner_visibility():
    """P1-014 — an owner with a deny-set tool must not see it."""
    from personal_ai_os.gateway.capabilities import CapabilityService
    from personal_ai_os.tool_broker import ToolBroker
    from tests.unit.test_registry import make_tool

    registry = ToolRegistry()
    registry.register(make_tool("safe.list", "safe", risk_level=1))
    registry.register(make_tool("secret.get", "secret", risk_level=1))
    caps = CapabilityService(registry, owner_deny={"owner-b": {"secret.get"}})
    broker = ToolBroker(
        registry=registry, policy_engine=FakePolicy(), credential_broker=FakeCredential(),
        capabilities=caps,
    )
    a = await broker.list_available_tools(owner_id="owner-a")
    assert any(t.name == "secret.get" for t in a)
    b = await broker.list_available_tools(owner_id="owner-b")
    assert not any(t.name == "secret.get" for t in b)
    assert any(t.name == "safe.list" for t in b)
    assert caps.can_use("owner-a", "secret.get")
    assert not caps.can_use("owner-b", "secret.get")


async def test_capability_risk_ceiling_is_enforced_at_execution():
    from personal_ai_os.gateway.capabilities import CapabilityService
    from personal_ai_os.tool_broker import ToolBroker
    from tests.unit.test_registry import make_tool

    registry = ToolRegistry()
    registry.register(make_tool("danger.run", "danger", risk_level=3))
    caps = CapabilityService(registry, risk_ceiling=1)
    broker = ToolBroker(
        registry=registry,
        policy_engine=FakePolicy(),
        credential_broker=FakeCredential(),
        capabilities=caps,
    )

    assert not caps.can_use("owner-a", "danger.run")
    result = await broker.execute("danger.run", {}, ctx(owner_id="owner-a"))
    assert result.success is False
    assert result.error_code == "CAPABILITY_DENIED"




async def test_result_data_size_is_bounded():
    """P1-001 — a connector cannot stuff an unbounded data dict into state."""
    big_data = {"entries": [{"x": "y" * 500} for _ in range(1000)]}
    connector = FakeConnector(result=ToolResult.ok(data=big_data))
    broker, registry, _ = make_broker(connector=connector, max_result_chars=2000)
    await register_echo(broker, connector)
    result = await broker.execute("fake.echo", {"message": "hi"}, ctx())
    assert result.success
    # the data was replaced by a bounded marker
    assert result.data.get("truncated") is True
    assert result.raw_size_bytes and result.raw_size_bytes > 0


def _r3_tool() -> ToolDescriptor:
    t = echo_tool()
    t.name = "mail.send"
    t.risk_level = 3
    t.side_effect = True
    return t


class R3Connector(FakeConnector):
    async def list_tools(self) -> list[ToolDescriptor]:
        return [_r3_tool()]


async def test_r3_execution_writes_durable_intent():
    """P1-033 — an R3 tool persists a durable intent before executing."""
    from sqlalchemy import select

    from personal_ai_os.db.models import AuditEvent, Run, User
    from personal_ai_os.db.session import session_scope

    owner_id = uuid4()
    async with session_scope() as s:
        s.add(User(username=f"r3{uuid4().hex[:6]}", api_key=f"k{uuid4().hex[:8]}", id=owner_id))
        await s.flush()
        run_id = uuid4()
        s.add(Run(id=run_id, owner_id=owner_id, status="running", input={}))
        await s.flush()
    connector = R3Connector()
    broker, registry, _ = make_broker(connector=connector)
    await broker.register_connector(connector)
    result = await broker.execute(
        "mail.send", {"message": "hi"}, ctx(owner_id=owner_id, run_id=run_id)
    )
    assert result.success
    assert connector.executed  # the connector ran
    async with session_scope() as s:
        intents = (await s.execute(
            select(AuditEvent).where(AuditEvent.event_type == "tool.intent")
        )).scalars().all()
        assert intents, "expected a durable tool.intent record"
        assert intents[0].resource_id == "mail.send"


async def test_r3_intent_failure_fails_closed(monkeypatch):
    """P1-033 — if the durable intent cannot be persisted, the tool never runs."""
    connector = R3Connector()
    broker, registry, _ = make_broker(connector=connector)
    await broker.register_connector(connector)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _boom(*a, **k):
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr("personal_ai_os.db.session.session_scope", _boom)
    result = await broker.execute("mail.send", {"message": "hi"}, ctx())
    assert result.success is False
    assert result.error_code == "POLICY_DENIED"
    assert connector.executed == []  # never executed
