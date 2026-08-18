"""End-to-end TUI provider setup across the API and runtime boundaries."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
from textual.widgets import Input, Select

from personal_ai_os.agent_runtime import RunRunner, build_graph
from personal_ai_os.cli.api.client import AsyncAPIClient
from personal_ai_os.cli.tui.app import PersonalAIApp
from personal_ai_os.cli.tui.screens.api_config import APIConfigScreen
from personal_ai_os.cli.tui.screens.model import ModelScreen
from personal_ai_os.cli.tui.widgets.composer import Composer
from personal_ai_os.context_engine import ContextEngine
from personal_ai_os.db.models import User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import ServiceContainer
from personal_ai_os.memory_engine import SQLMemoryStore
from personal_ai_os.model_gateway import (
    DeterministicEmbedding,
    OpenAICompatibleProvider,
    ProviderConfigStore,
    ProviderProfile,
    ProviderRuntimeService,
)
from personal_ai_os.model_gateway.runtime import build_provider_bundle
from personal_ai_os.policy_engine import ApprovalEngine, CredentialBroker, PolicyEngine
from personal_ai_os.scheduler import EventBus
from personal_ai_os.tool_broker import ToolBroker, ToolRegistry

_API_KEY = "provider-e2e-api-key"
_PROVIDER_KEY = "provider-e2e-secret"


class _MemoryVault:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, name: str, key: str) -> bool:
        self.values[name] = key
        return True

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def delete(self, name: str) -> bool:
        self.values.pop(name, None)
        return True


async def _wait_run(
    app: PersonalAIApp,
    pilot,
    previous_run_id: str | None,
) -> None:  # noqa: ANN001
    for _ in range(300):
        await pilot.pause()
        state = app.controller.state if app.controller is not None else None
        if (
            state is not None
            and state.run_id != previous_run_id
            and state.run_status in {"completed", "failed", "cancelled"}
            and not app.controller.busy
        ):
            assert state.run_status == "completed"
            return
        await asyncio.sleep(0.01)
    raise AssertionError("TUI run did not finish")


async def _open_screen(app: PersonalAIApp, pilot, command: str, screen_type):  # noqa: ANN001
    composer = app.query_one("#composer", Composer)
    composer.text = command
    await pilot.press("enter")
    for _ in range(100):
        await pilot.pause()
        if isinstance(app.screen, screen_type):
            return app.screen
        await asyncio.sleep(0.005)
    raise AssertionError(f"{command} screen did not open")


async def _wait_screen_closed(app: PersonalAIApp, pilot, screen_type) -> None:  # noqa: ANN001
    for _ in range(200):
        await pilot.pause()
        if not isinstance(app.screen, screen_type):
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"{screen_type.__name__} did not close")


async def _build_stack(tmp_path):
    provider_requests: list[dict] = []

    def provider_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {_PROVIDER_KEY}"
        if request.method == "GET" and request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "deepseek-chat"},
                        {"id": "deepseek-reasoner"},
                    ]
                },
            )
        body = json.loads(request.content)
        provider_requests.append(body)
        answer = f"fake provider used {body['model']}"
        stream = (
            "data: "
            + json.dumps(
                {
                    "choices": [
                        {
                            "delta": {"content": answer},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 4},
                }
            )
            + "\n\ndata: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            text=stream,
            headers={"content-type": "text/event-stream"},
        )

    provider_transport = httpx.MockTransport(provider_handler)

    def bundle_factory(profile: ProviderProfile | None):
        bundle = build_provider_bundle(profile)
        if isinstance(bundle.adapter, OpenAICompatibleProvider):
            bundle.adapter._transport = provider_transport
        return bundle

    store = ProviderConfigStore(
        path=tmp_path / "config" / "profiles.json",
        vault=_MemoryVault(),
    )
    runtime = ProviderRuntimeService(store=store, bundle_factory=bundle_factory)

    event_bus = EventBus()
    policy = PolicyEngine()
    approval = ApprovalEngine()
    registry = ToolRegistry()
    broker = ToolBroker(
        registry=registry,
        policy_engine=policy,
        credential_broker=CredentialBroker(source={}),
        approval_engine=approval,
        event_bus=event_bus,
    )
    memory = SQLMemoryStore(
        embedding_provider=DeterministicEmbedding(),
        event_bus=event_bus,
    )
    context_engine = ContextEngine(memory_store=memory, tool_registry=registry)
    graph = build_graph(
        context_engine=context_engine,
        model_provider=runtime.provider,
        tool_broker=broker,
        memory_store=memory,
    )
    runner = RunRunner(
        graph=graph,
        model_provider=runtime.provider,
        tool_broker=broker,
        memory_store=memory,
        context_engine=context_engine,
        policy_engine=policy,
        approval_engine=approval,
        event_bus=event_bus,
    )
    async with session_scope() as session:
        session.add(
            User(
                username=f"provider-e2e-{uuid.uuid4().hex[:8]}",
                api_key=_API_KEY,
            )
        )
        await session.flush()

    services = ServiceContainer(
        event_bus=event_bus,
        model_provider=runtime.provider,
        provider_runtime=runtime,
        tool_registry=registry,
        tool_broker=broker,
        policy_engine=policy,
        approval_engine=approval,
        memory_store=memory,
        context_engine=context_engine,
        runner=runner,
    )
    return store, runtime, services, provider_requests


async def test_echo_to_provider_model_hot_switch_preserves_tui_session(
    tmp_path,
):
    from apps.api.main import create_app

    store, runtime, services, provider_requests = await _build_stack(tmp_path)
    api_app = create_app(services=services)

    async with api_app.router.lifespan_context(api_app):
        client = AsyncAPIClient(
            base_url="http://localhost",
            api_key=_API_KEY,
            transport=httpx.ASGITransport(app=api_app),
        )
        app = PersonalAIApp(client=client, provider_store=store)

        async with app.run_test() as pilot:
            assert app.controller is not None
            assert runtime.status.mode == "echo"
            assert app.controller.state.provider_status.is_echo is True
            assert app.query_one("#provider-notice").display is True

            composer = app.query_one("#composer", Composer)
            previous_run_id = app.controller.state.run_id
            composer.text = "before setup"
            await pilot.press("enter")
            await _wait_run(app, pilot, previous_run_id)

            session_id = app.controller.state.session_id
            transcript_before = list(app.controller.state.transcript)
            assert session_id is not None
            assert any(
                "[Echo demo response]" in cell.text
                for cell in transcript_before
            )

            api_screen = await _open_screen(
                app, pilot, "/api", APIConfigScreen
            )
            assert (
                api_screen.query_one("#api-base-url", Input).value
                == "https://api.deepseek.com/v1"
            )
            api_screen.query_one("#api-key", Input).value = _PROVIDER_KEY
            api_screen.query_one("#api-save").press()
            await _wait_screen_closed(app, pilot, APIConfigScreen)

            assert runtime.status.mode == "provider"
            assert runtime.status.model == "deepseek-chat"
            assert app.controller.state.session_id == session_id
            assert app.controller.state.transcript == transcript_before

            model_screen = await _open_screen(
                app, pilot, "/model", ModelScreen
            )
            model_screen.query_one("#model-select", Select).value = (
                "deepseek-reasoner"
            )
            model_screen.action_save()
            await _wait_screen_closed(app, pilot, ModelScreen)

            assert runtime.status.model == "deepseek-reasoner"
            assert store.get_active().model == "deepseek-reasoner"
            assert app.controller.state.session_id == session_id
            assert app.controller.state.transcript == transcript_before

            previous_run_id = app.controller.state.run_id
            composer.text = "after setup"
            await pilot.press("enter")
            await _wait_run(app, pilot, previous_run_id)

            assert app.controller.state.session_id == session_id
            assert app.controller.state.transcript[: len(transcript_before)] == (
                transcript_before
            )
            assert any(
                cell.text == "fake provider used deepseek-reasoner"
                for cell in app.controller.state.transcript
            )

        await client.aclose()

    assert provider_requests
    assert provider_requests[-1]["model"] == "deepseek-reasoner"
    assert _PROVIDER_KEY not in store.path.read_text(encoding="utf-8")
