from __future__ import annotations

import json

import httpx
import pytest

from personal_ai_os.common.models import ModelRequest, ModelResponse
from personal_ai_os.db.models import Run, User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import has_active_runs
from personal_ai_os.model_gateway import (
    OpenAICompatibleProvider,
    ProviderBusyError,
    ProviderConfigStore,
    ProviderProfile,
    ProviderReloadError,
    ProviderRuntimeService,
    ReloadableProvider,
    provider_capabilities,
    provider_identity,
)
from personal_ai_os.model_gateway.runtime import (
    ProviderCapabilities,
    ProviderStatus,
    _ProviderBundle,
)


class _MemoryVault:
    def __init__(self):
        self.values: dict[str, str] = {}

    def set(self, name: str, key: str) -> bool:
        self.values[name] = key
        return True

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def delete(self, name: str) -> bool:
        self.values.pop(name, None)
        return True


class _TaggedProvider:
    provider_name = "tagged"

    def __init__(self, tag: str, *, healthy: bool = True):
        self.tag = tag
        self.healthy = healthy

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=self.tag,
            tool_calls=None,
            model=self.tag,
            provider=self.tag,
        )

    async def health_check(self) -> bool:
        return self.healthy

    async def list_models(self) -> list[str]:
        return [f"{self.tag}-a", f"{self.tag}-b"]


def _store(tmp_path) -> ProviderConfigStore:
    return ProviderConfigStore(path=tmp_path / "config" / "profiles.json", vault=_MemoryVault())


def _factory(profile: ProviderProfile | None) -> _ProviderBundle:
    if profile is None:
        provider = _TaggedProvider("echo")
        return _ProviderBundle(
            routed=provider,
            adapter=provider,
            status=ProviderStatus(
                mode="echo",
                profile=None,
                format=None,
                provider="echo",
                model="echo",
                reasoning_effort="auto",
                capabilities=ProviderCapabilities(False, ("auto",)),
            ),
        )
    provider = _TaggedProvider(profile.model, healthy=profile.model != "invalid")
    return _ProviderBundle(
        routed=provider,
        adapter=provider,
        status=ProviderStatus(
            mode="provider",
            profile=profile.name,
            format=profile.format,
            provider=provider_identity(profile),
            model=profile.model,
            reasoning_effort=profile.reasoning_effort,
            capabilities=ProviderCapabilities(True, ("auto",)),
        ),
    )


def _request() -> ModelRequest:
    return ModelRequest(purpose="assistant", messages=[{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_empty_store_uses_echo_provider(tmp_path):
    runtime = ProviderRuntimeService(store=_store(tmp_path))

    assert runtime.status.mode == "echo"
    response = await runtime.provider.complete(_request())
    assert response.provider == "echo"
    assert response.content == "Echo: hi"


def test_custom_profile_exposes_deepseek_identity_without_secret(tmp_path):
    store = _store(tmp_path)
    store.add(
        ProviderProfile(
            name="work",
            format="openai",
            api_key="runtime-secret-must-not-leak",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-reasoner",
            reasoning_effort="auto",
        )
    )

    status = ProviderRuntimeService(store=store).status
    serialized = json.dumps(status.to_dict())

    assert status.profile == "work"
    assert status.provider == "deepseek"
    assert status.model == "deepseek-reasoner"
    assert status.reasoning_effort == "auto"
    assert "runtime-secret-must-not-leak" not in serialized
    assert "api_key" not in serialized


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("", "openai"),
        ("https://api.openai.com/custom/path", "openai"),
        ("https://api.deepseek.com/v1", "deepseek"),
        ("https://api.moonshot.cn/v1", "moonshot"),
        ("https://open.bigmodel.cn/api/paas/v4", "zhipu"),
        ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen"),
        ("http://localhost:11434/v1", "ollama"),
        ("https://llm.example.com/v1", "openai-compatible"),
    ],
)
def test_provider_identity_uses_endpoint_not_profile_name(base_url, expected):
    profile = ProviderProfile(
        name="misleading-custom-name",
        format="openai",
        base_url=base_url,
    )

    assert provider_identity(profile) == expected


def test_anthropic_identity_uses_format_not_profile_name():
    profile = ProviderProfile(name="work", format="anthropic")

    assert provider_identity(profile) == "anthropic"


@pytest.mark.asyncio
async def test_reload_atomically_updates_shared_proxy(tmp_path):
    store = _store(tmp_path)
    store.add(ProviderProfile(name="one", format="openai", api_key="key", model="one"))
    runtime = ProviderRuntimeService(store=store, bundle_factory=_factory)
    shared_proxy = runtime.provider

    store.add(
        ProviderProfile(name="two", format="openai", api_key="key", model="two"),
        activate=True,
    )
    status = await runtime.reload()

    assert runtime.provider is shared_proxy
    assert status.model == "two"
    assert (await shared_proxy.complete(_request())).content == "two"


@pytest.mark.asyncio
async def test_failed_reload_keeps_previous_provider(tmp_path):
    store = _store(tmp_path)
    store.add(ProviderProfile(name="one", format="openai", api_key="key", model="one"))
    runtime = ProviderRuntimeService(store=store, bundle_factory=_factory)
    store.update("one", model="invalid")

    with pytest.raises(ProviderReloadError, match="validation failed"):
        await runtime.reload()

    assert runtime.status.model == "one"
    assert (await runtime.provider.complete(_request())).content == "one"


@pytest.mark.asyncio
async def test_active_run_rejects_reload(tmp_path):
    store = _store(tmp_path)
    store.add(ProviderProfile(name="one", format="openai", api_key="key", model="one"))
    runtime = ProviderRuntimeService(
        store=store,
        active_run_checker=lambda: True,
        bundle_factory=_factory,
    )

    with pytest.raises(ProviderBusyError, match="run is active"):
        await runtime.reload()


@pytest.mark.asyncio
async def test_database_active_run_rejects_reload(tmp_path):
    store = _store(tmp_path)
    store.add(ProviderProfile(name="one", format="openai", api_key="key", model="one"))
    async with session_scope() as session:
        user = User(username="provider-active-run")
        session.add(user)
        await session.flush()
        session.add(Run(owner_id=user.id, status="running", input={}))
        await session.flush()
    runtime = ProviderRuntimeService(
        store=store,
        active_run_checker=has_active_runs,
        bundle_factory=_factory,
    )

    with pytest.raises(ProviderBusyError, match="run is active"):
        await runtime.reload()


@pytest.mark.asyncio
async def test_proxy_pin_keeps_one_snapshot_for_a_run():
    old = _TaggedProvider("old")
    proxy = ReloadableProvider(old)

    with proxy.pin():
        proxy.replace(_TaggedProvider("new"))
        assert (await proxy.complete(_request())).content == "old"

    assert (await proxy.complete(_request())).content == "new"


def test_reasoning_effort_capabilities_are_conservative():
    deepseek = ProviderProfile(
        name="deepseek",
        format="openai",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-reasoner",
    )
    openai = ProviderProfile(
        name="openai",
        format="openai",
        base_url="https://api.openai.com/v1",
        model="o3",
    )

    assert provider_capabilities(deepseek, deepseek.model).reasoning_efforts == ("auto",)
    assert provider_capabilities(openai, openai.model).reasoning_efforts == (
        "auto",
        "low",
        "medium",
        "high",
    )


@pytest.mark.asyncio
async def test_openai_model_enumeration_and_reasoning_effort_payload():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"data": [{"id": "o3"}, {"id": "gpt-5"}, {"id": "o3"}]},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {},
            },
            request=request,
        )

    provider = OpenAICompatibleProvider(
        api_key="secret",
        default_model="o3",
        reasoning_effort="high",
        transport=httpx.MockTransport(handler),
    )

    assert await provider.list_models() == ["gpt-5", "o3"]
    await provider.complete(_request())
    assert json.loads(requests[-1].content)["reasoning_effort"] == "high"
