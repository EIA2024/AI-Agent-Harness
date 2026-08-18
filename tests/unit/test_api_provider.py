from __future__ import annotations

import uuid

import pytest

from personal_ai_os.common.models import ModelRequest, ModelResponse
from personal_ai_os.db.models import User
from personal_ai_os.db.session import session_scope
from personal_ai_os.gateway.services import ServiceContainer
from personal_ai_os.model_gateway import (
    ProviderConfigStore,
    ProviderProfile,
    ProviderRuntimeService,
    provider_identity,
)
from personal_ai_os.model_gateway.runtime import (
    ProviderCapabilities,
    ProviderStatus,
    _ProviderBundle,
)

_API_KEY = "provider-control-api-key"
_AUTH = {"X-API-Key": _API_KEY}
_PROVIDER_KEY = "provider-secret-must-not-leak"


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


class _Provider:
    provider_name = "test"

    def __init__(self, model: str):
        self.model = model

    async def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=self.model,
            tool_calls=None,
            model=self.model,
            provider="test",
        )

    async def health_check(self) -> bool:
        return self.model != "invalid"

    async def list_models(self) -> list[str]:
        return ["deepseek-chat", "deepseek-reasoner"]


def _factory(profile: ProviderProfile | None) -> _ProviderBundle:
    assert profile is not None
    provider = _Provider(profile.model)
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


async def _add_user() -> None:
    async with session_scope() as session:
        session.add(User(username=f"provider-{uuid.uuid4().hex[:8]}", api_key=_API_KEY))
        await session.flush()


def _runtime(tmp_path, *, active_run_checker=None):
    store = ProviderConfigStore(
        path=tmp_path / "shared" / "profiles.json",
        vault=_MemoryVault(),
    )
    store.add(
        ProviderProfile(
            name="work",
            format="openai",
            api_key=_PROVIDER_KEY,
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
        )
    )
    runtime = ProviderRuntimeService(
        store=store,
        active_run_checker=active_run_checker,
        bundle_factory=_factory,
    )
    services = ServiceContainer(
        model_provider=runtime.provider,
        provider_runtime=runtime,
        runner=object(),
    )
    return store, runtime, services


@pytest.mark.asyncio
async def test_provider_api_auth_status_models_and_update(make_api, tmp_path):
    await _add_user()
    store, runtime, services = _runtime(tmp_path)
    config_dir = str(store.path.parent)

    async with make_api(services=services) as client:
        missing = await client.get("/v1/provider")
        wrong = await client.get(
            "/v1/provider", headers={"X-API-Key": "wrong"}
        )
        status = await client.get("/v1/provider", headers=_AUTH)
        models = await client.get("/v1/provider/models", headers=_AUTH)
        boundary = await client.post(
            "/v1/provider/reload",
            json={"config_dir": str(tmp_path / "other")},
            headers=_AUTH,
        )
        updated = await client.put(
            "/v1/provider/model",
            json={
                "config_dir": config_dir,
                "model": "deepseek-reasoner",
                "reasoning_effort": "auto",
            },
            headers=_AUTH,
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert status.status_code == 200
    assert status.json()["profile"] == "work"
    assert status.json()["provider"] == "deepseek"
    assert status.json()["model"] == "deepseek-chat"
    assert status.json()["reasoning_effort"] == "auto"
    assert models.json()["provider"] == "deepseek"
    assert models.json()["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert boundary.status_code == 412
    assert updated.status_code == 200
    assert updated.json()["profile"] == "work"
    assert updated.json()["provider"] == "deepseek"
    assert updated.json()["model"] == "deepseek-reasoner"
    assert updated.json()["reasoning_effort"] == "auto"
    assert runtime.status.model == "deepseek-reasoner"
    assert store.get_active().model == "deepseek-reasoner"
    all_responses = " ".join(
        response.text for response in (status, models, boundary, updated)
    )
    assert _PROVIDER_KEY not in all_responses
    assert "api_key" not in all_responses


@pytest.mark.asyncio
async def test_provider_api_failed_update_rolls_back_and_active_run_rejects(
    make_api, tmp_path
):
    await _add_user()
    active = False

    def has_active_run() -> bool:
        return active

    store, runtime, services = _runtime(
        tmp_path, active_run_checker=has_active_run
    )
    body = {"config_dir": str(store.path.parent)}

    async with make_api(services=services) as client:
        failed = await client.put(
            "/v1/provider/model",
            json=body | {"model": "invalid", "reasoning_effort": "auto"},
            headers=_AUTH,
        )
        active = True
        busy = await client.post(
            "/v1/provider/reload",
            json=body,
            headers=_AUTH,
        )

    assert failed.status_code == 422
    assert runtime.status.model == "deepseek-chat"
    assert store.get_active().model == "deepseek-chat"
    assert busy.status_code == 409
    assert _PROVIDER_KEY not in failed.text + busy.text
