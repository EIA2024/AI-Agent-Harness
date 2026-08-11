"""Tests for model_gateway: providers, router routing and failover."""

from __future__ import annotations

import pytest

from personal_ai_os.common.models import ModelError, ModelRequest, ModelResponse
from personal_ai_os.model_gateway import (
    AnthropicProvider,
    EchoProvider,
    FakeProvider,
    ModelRouter,
)


def _request(**overrides) -> ModelRequest:
    kwargs = dict(
        purpose="assistant",
        messages=[{"role": "user", "content": "你好"}],
    )
    kwargs.update(overrides)
    return ModelRequest(**kwargs)


# ---------------------------------------------------------------------------
# FakeProvider
# ---------------------------------------------------------------------------


async def test_fake_provider_script_progression():
    script = [
        {"content": "first"},
        {"content": "second"},
        {"tool_calls": [{"name": "tool1", "arguments": {"x": 1}}]},
    ]
    prov = FakeProvider(script=script)
    assert prov.provider_name == "fake"

    r1 = await prov.complete(_request())
    assert r1.content == "first"
    assert r1.provider == "fake"

    r2 = await prov.complete(_request())
    assert r2.content == "second"

    r3 = await prov.complete(_request())
    assert r3.content is None
    assert r3.tool_calls == [{"name": "tool1", "arguments": {"x": 1}}]
    assert r3.finish_reason == "tool_calls"

    # exhausted -> empty content
    r4 = await prov.complete(_request())
    assert r4.content == ""
    assert prov.call_count == 4
    assert await prov.health_check() is True


async def test_fake_provider_records_usage():
    script = [
        {"content": "ok", "usage": {"input_tokens": 100, "output_tokens": 20, "cached_tokens": 40}}
    ]
    prov = FakeProvider(script=script)
    resp = await prov.complete(_request(preferred_model="claude-sonnet-5"))
    assert resp.usage.input_tokens == 100
    assert resp.usage.cached_tokens == 40
    assert resp.usage.output_tokens == 20
    assert resp.usage.total_tokens == 160
    # claude-sonnet-5 pricing is known -> cost > 0
    assert resp.usage.cost_usd > 0


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


async def test_anthropic_provider_raises_model_error_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    prov = AnthropicProvider(api_key=None)
    with pytest.raises(ModelError, match="API key"):
        await prov.complete(_request())


async def test_anthropic_provider_health_check_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    prov = AnthropicProvider(api_key=None)
    assert await prov.health_check() is False


# ---------------------------------------------------------------------------
# EchoProvider
# ---------------------------------------------------------------------------


async def test_echo_provider_returns_last_user_message():
    prov = EchoProvider()
    resp = await prov.complete(_request(messages=[{"role": "user", "content": "回显我"}]))
    assert resp.provider == "echo"
    assert "回显我" in resp.content


# ---------------------------------------------------------------------------
# ModelRouter
# ---------------------------------------------------------------------------


def _router_with_two_providers() -> tuple[ModelRouter, FakeProvider, FakeProvider]:
    haiku = FakeProvider(script=[{"content": "haiku-ok"}], models=["claude-haiku-4-5"])
    sonnet = FakeProvider(script=[{"content": "sonnet-ok"}], models=["claude-sonnet-5"])
    router = ModelRouter()
    router.add_provider(haiku, name="haiku")
    router.add_provider(sonnet, name="sonnet")
    return router, haiku, sonnet


def test_purpose_role_mapping():
    assert ModelRouter.role_for_purpose("intent_classification") == "router"
    assert ModelRouter.role_for_purpose("memory_extraction") == "router"
    assert ModelRouter.role_for_purpose("assistant") == "worker"
    assert ModelRouter.role_for_purpose("reasoning") == "worker"
    assert ModelRouter.role_for_purpose("planning") == "worker"
    assert ModelRouter.role_for_purpose("vision") == "vision"
    assert ModelRouter.role_for_purpose("embedding") == "embedding"


def test_router_selects_by_purpose():
    router, haiku, sonnet = _router_with_two_providers()

    provider, model = router.select(_request(purpose="intent_classification"))
    assert provider is haiku
    assert model == "claude-haiku-4-5"

    provider, model = router.select(_request(purpose="assistant"))
    assert provider is sonnet
    assert model == "claude-sonnet-5"

    provider, model = router.select(_request(purpose="reasoning", quality_class="max"))
    assert provider is sonnet
    assert model == "claude-sonnet-5"

    provider, model = router.select(_request(purpose="vision"))
    assert provider is sonnet
    assert model == "claude-sonnet-5"


def test_router_preferred_provider_and_model():
    router, haiku, sonnet = _router_with_two_providers()

    provider, model = router.select(_request(purpose="assistant", preferred_model="claude-haiku-4-5"))
    assert provider is haiku
    assert model == "claude-haiku-4-5"

    provider, model = router.select(
        _request(purpose="assistant", preferred_provider=sonnet.provider_name)
    )
    assert provider is sonnet


def test_router_falls_back_to_first_provider_when_no_role_model():
    router = ModelRouter()
    fake = FakeProvider(script=[{"content": "x"}])  # wildcard models
    router.add_provider(fake)
    provider, model = router.select(_request(purpose="assistant"))
    assert provider is fake


async def test_router_complete_routes_to_correct_model():
    router, haiku, sonnet = _router_with_two_providers()
    resp = await router.complete(_request(purpose="assistant"))
    assert resp.content == "sonnet-ok"
    assert resp.model == "claude-sonnet-5"
    assert resp.provider == "fake"
    assert resp.usage.total_tokens > 0


class _FailingProvider(FakeProvider):
    """Fake provider that always raises ModelError."""

    def __init__(self, *, models=None, name="fail"):
        super().__init__(script=[{"content": "never"}], models=models)
        self.provider_name = name

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        raise ModelError("simulated upstream failure")


async def test_router_failover_to_next_provider():
    fail = _FailingProvider(models=["claude-sonnet-5"])
    good = FakeProvider(script=[{"content": "recovered"}], models=["claude-sonnet-5"])
    router = ModelRouter()
    router.add_provider(fail)
    router.add_provider(good)

    resp = await router.complete(_request(purpose="assistant"))
    assert resp.content == "recovered"
    assert resp.provider == "fake"
    assert fail.call_count == 1
    assert good.call_count == 1


async def test_router_all_providers_fail_raises_model_error():
    fail1 = _FailingProvider(models=["claude-sonnet-5"], name="fail1")
    fail2 = _FailingProvider(models=["claude-sonnet-5"], name="fail2")
    router = ModelRouter()
    router.add_provider(fail1)
    router.add_provider(fail2)

    with pytest.raises(ModelError, match="All providers failed"):
        await router.complete(_request(purpose="assistant"))


async def test_router_records_cost_usage():
    script = [{"content": "hi", "usage": {"input_tokens": 50, "output_tokens": 10}}]
    prov = FakeProvider(script=script, models=["claude-sonnet-5"])
    router = ModelRouter(providers={prov.provider_name: prov})

    resp = await router.complete(_request(purpose="assistant", max_cost=1.0))
    assert resp.model == "claude-sonnet-5"
    assert resp.usage.input_tokens == 50
    assert resp.usage.output_tokens == 10
    assert resp.usage.cost_usd > 0
    assert resp.usage.total_tokens == 60


async def test_router_no_providers_raises():
    router = ModelRouter()
    with pytest.raises(ModelError, match="No model providers"):
        router.select(_request())


async def test_anthropic_message_conversion_helpers():
    from personal_ai_os.model_gateway.provider import (
        split_system_and_messages,
        to_anthropic_messages,
    )

    system, rest = split_system_and_messages(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ]
    )
    assert system == "You are helpful."
    assert rest == [{"role": "user", "content": "hi"}]

    converted = to_anthropic_messages(
        [
            {"role": "user", "content": "run tool"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "name": "calc", "arguments": {"x": 1}}]},
            {"role": "tool", "tool_call_id": "tc1", "content": "42"},
        ]
    )
    assert converted[0] == {"role": "user", "content": "run tool"}
    assert converted[1]["role"] == "assistant"
    assert converted[1]["content"][0]["type"] == "tool_use"
    assert converted[1]["content"][0]["name"] == "calc"
    assert converted[2]["role"] == "user"
    assert converted[2]["content"][0]["type"] == "tool_result"
    assert converted[2]["content"][0]["tool_use_id"] == "tc1"
