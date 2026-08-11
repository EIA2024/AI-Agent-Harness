"""Integration fixtures: real runtime + scripted model behind an in-process ASGI app.

The CLI's ``AsyncAPIClient`` talks to the real FastAPI app over
``ASGITransport``, so ``exec`` end-to-end (message → run → stream → reduce →
render) is exercised for real — no HTTP mocks.
"""

from __future__ import annotations

import uuid

import pytest

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
from personal_ai_os.db.models import User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import ServiceContainer
from personal_ai_os.memory_engine import SQLMemoryStore
from personal_ai_os.model_gateway import DeterministicEmbedding
from personal_ai_os.policy_engine import ApprovalEngine, CredentialBroker, PolicyEngine
from personal_ai_os.scheduler import EventBus
from personal_ai_os.tool_broker import ToolBroker, ToolRegistry

API_KEY = "cli-test-key"


class ScriptedModel:
    """Emits scripted responses / tool calls in order (plain answer by default)."""

    def __init__(self, script: list[dict] | None = None) -> None:
        self.script = script or [{"content": "hello world"}]
        self.i = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
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
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
                risk_level=3,
                side_effect=True,
                external_write=True,
            ),
            ToolDescriptor(
                name="calculator.evaluate",
                namespace="calculator",
                description="Evaluate an arithmetic expression (R0 auto)",
                input_schema={
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
                risk_level=0,
            ),
        ]

    async def execute(
        self, tool: str, arguments: dict, ctx: ToolExecutionContext
    ) -> ToolResult:
        if tool == "mail.send":
            return ToolResult.ok(data={"sent": True, "to": arguments.get("to")})
        if tool == "calculator.evaluate":
            expr = arguments.get("expression", "")
            try:
                value = eval(expr, {"__builtins__": {}})  # noqa: S307 - test double only
                return ToolResult.ok(data={"result": value})
            except Exception as exc:  # noqa: BLE001
                return ToolResult.fail(error=str(exc))
        return ToolResult.fail(error=f"unknown tool {tool}")


def _tool_call(name: str, arguments: dict) -> list[dict]:
    return [
        {
            "type": "function",
            "id": f"call-{uuid.uuid4().hex[:8]}",
            "function": {"name": name, "arguments": arguments},
        }
    ]


async def _make_owner() -> uuid.UUID:
    async with session_scope() as session:
        user = User(username=f"cli{uuid.uuid4().hex[:8]}", api_key=API_KEY)
        session.add(user)
        await session.flush()
        return user.id


async def build_stack(script: list[dict] | None = None) -> ServiceContainer:
    """Wire a real runtime (no core mocks) behind the API app; return the app."""

    from apps.api.main import create_app

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

    await _make_owner()

    container = ServiceContainer(
        event_bus=event_bus,
        model_provider=model,
        tool_registry=registry,
        tool_broker=broker,
        policy_engine=policy,
        approval_engine=approval,
        credential_broker=credentials,
        memory_store=memory,
        context_engine=context_engine,
        runner=runner,
    )
    app = create_app(services=container)
    return app


@pytest.fixture
async def cli_client(monkeypatch: pytest.MonkeyPatch):
    """AsyncAPIClient wired to a real in-process app via ASGITransport."""
    from contextlib import asynccontextmanager

    from httpx import ASGITransport

    from personal_ai_os.cli.api.client import AsyncAPIClient
    from tests.conftest import _dispose_engine

    @asynccontextmanager
    async def _make(script: list[dict] | None = None):
        app = await build_stack(script)
        async with app.router.lifespan_context(app):
            client = AsyncAPIClient(
                base_url="http://test",
                api_key=API_KEY,
                transport=ASGITransport(app=app),
            )
            yield client
            await client.aclose()
        await _dispose_engine()

    return _make
