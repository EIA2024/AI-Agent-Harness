"""Tests for the provider profile config store and the OpenAI-compatible provider."""

from __future__ import annotations

import json

import httpx
import pytest

from personal_ai_os.common.models import ModelRequest, ModelResponse, ToolDescriptor
from personal_ai_os.model_gateway import (
    OpenAICompatibleProvider,
    ProviderConfigStore,
    ProviderProfile,
)

# ---------------------------------------------------------------------------
# ProviderConfigStore
# ---------------------------------------------------------------------------


def _make_store(tmp_path):
    return ProviderConfigStore(path=tmp_path / "nested" / "profiles.json")


def test_add_and_get_active(tmp_path):
    store = _make_store(tmp_path)
    store.add(ProviderProfile(name="deepseek", format="openai", api_key="sk-ds-12345678",
                              base_url="https://api.deepseek.com/v1", model="deepseek-chat"))
    assert store.get_active_name() == "deepseek"
    active = store.get_active()
    assert active is not None and active.format == "openai"
    assert active.base_url == "https://api.deepseek.com/v1"


def test_multiple_profiles_and_switch(tmp_path):
    store = _make_store(tmp_path)
    store.add(ProviderProfile(name="a", format="openai", api_key="key-a", model="gpt-4o"))
    store.add(ProviderProfile(name="b", format="anthropic", api_key="key-b", model="claude-sonnet-5"),
              activate=False)
    # b should NOT be active since we asked not to activate
    assert store.get_active_name() == "a"
    assert store.set_active("b")
    assert store.get_active_name() == "b"
    assert store.get_active().model == "claude-sonnet-5"


def test_edit_and_remove(tmp_path):
    store = _make_store(tmp_path)
    store.add(ProviderProfile(name="x", format="openai", api_key="k", model="m1"))
    store.update("x", model="m2")
    assert store.get("x").model == "m2"
    store.update("x", api_key="new-secret-1234")
    assert store.get("x").api_key == "new-secret-1234"
    assert store.remove("x")
    assert store.get("x") is None
    assert store.get_active() is None  # active cleared when last removed


def test_masked_key_never_leaks(tmp_path):
    store = _make_store(tmp_path)
    store.add(ProviderProfile(name="s", format="openai", api_key="sk-abcdefghijklmnopqrstuvwxyz1234"))
    masked = store.get("s").masked_key()
    assert "sk-abcdefghijklmnopqrstuvwxyz1234" not in masked
    assert masked.startswith("sk-a") and masked.endswith("1234")


def test_invalid_format_rejected():
    with pytest.raises(ValueError):
        ProviderProfile(name="bad", format="google")


def test_corrupt_file_tolerated(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text("{ not valid json", encoding="utf-8")
    store = ProviderConfigStore(path=path)
    assert store.list_profiles() == []
    assert store.get_active() is None


# ---------------------------------------------------------------------------
# OpenAICompatibleProvider
# ---------------------------------------------------------------------------


def _mock_transport(json_body: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=json_body, request=request)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_openai_complete_content():
    provider = OpenAICompatibleProvider(
        api_key="sk-test", base_url="https://api.deepseek.com/v1", default_model="deepseek-chat",
        transport=_mock_transport(
            {
                "choices": [{"message": {"content": "你好！"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        ),
    )
    resp = await provider.complete(ModelRequest(purpose="assistant", messages=[{"role": "user", "content": "hi"}]))
    assert isinstance(resp, ModelResponse)
    assert resp.content == "你好！"
    assert resp.provider == "openai_compatible"
    assert resp.usage.input_tokens == 10 and resp.usage.output_tokens == 5
    assert resp.finish_reason == "stop"


@pytest.mark.asyncio
async def test_openai_complete_tool_calls_native_format():
    provider = OpenAICompatibleProvider(
        api_key="sk-test", base_url="https://api.openai.com/v1",
        transport=_mock_transport(
            {
                "choices": [{
                    "message": {
                        "content": None,
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "calculator.evaluate", "arguments": '{"expression": "1+1"}'},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            }
        ),
    )
    resp = await provider.complete(ModelRequest(purpose="assistant", messages=[{"role": "user", "content": "1+1"}]))
    assert resp.tool_calls is not None
    fn = resp.tool_calls[0]["function"]
    assert fn["name"] == "calculator.evaluate"
    assert resp.finish_reason == "tool_calls"
    # runtime's _extract_tool_call_fields must be able to parse this shape
    import json as _json

    args = _json.loads(fn["arguments"])
    assert args == {"expression": "1+1"}


@pytest.mark.asyncio
async def test_openai_http_error_normalized():
    provider = OpenAICompatibleProvider(
        api_key="sk-test",
        transport=_mock_transport({"error": {"message": "bad key"}}, status=401),
    )
    from personal_ai_os.common.models import ModelError

    with pytest.raises(ModelError):
        await provider.complete(ModelRequest(purpose="assistant", messages=[{"role": "user", "content": "hi"}]))


@pytest.mark.asyncio
async def test_openai_passes_tools_and_system():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}, request=request)

    provider = OpenAICompatibleProvider(api_key="sk-secret", transport=httpx.MockTransport(handler))
    tool = ToolDescriptor(
        name="calculator.evaluate", namespace="calculator", description="calc",
        input_schema={"type": "object", "properties": {"expression": {"type": "string"}}},
    )
    await provider.complete(
        ModelRequest(
            purpose="assistant",
            messages=[{"role": "system", "content": "be brief"}, {"role": "user", "content": "1+1"}],
            tools=[tool.to_llm_schema()],
            temperature=0.2,
            max_tokens=64,
        )
    )
    assert captured["auth"] == "Bearer sk-secret"
    assert captured["body"]["temperature"] == 0.2
    assert captured["body"]["max_tokens"] == 64
    # namespaced tool name is sanitized (dot -> underscore) for strict endpoints
    assert captured["body"]["tools"][0]["function"]["name"] == "calculator_evaluate"
    assert captured["body"]["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_openai_tool_name_round_trip():
    """A sanitized tool name sent to the endpoint is mapped back to the original
    in the returned tool_calls (so the runtime can look it up in the registry)."""
    from personal_ai_os.model_gateway.provider import _sanitize_messages, _sanitize_tool_name

    assert _sanitize_tool_name("calculator.evaluate") == "calculator_evaluate"
    assert _sanitize_tool_name("github.delete_repo") == "github_delete_repo"

    # message-history tool frames are sanitized too (strict endpoints validate them)
    msgs = _sanitize_messages([
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "calculator.evaluate", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "calculator.evaluate", "content": "4"},
    ])
    assert msgs[0]["tool_calls"][0]["function"]["name"] == "calculator_evaluate"
    assert msgs[1]["name"] == "calculator_evaluate"
    # original not mutated
    assert msgs is not None

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent_name = body["tools"][0]["function"]["name"]
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "content": None,
                        "tool_calls": [{
                            "id": "c1", "type": "function",
                            "function": {"name": sent_name, "arguments": '{"expression": "1+1"}'},
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {},
            },
            request=request,
        )

    provider = OpenAICompatibleProvider(api_key="sk-test", transport=httpx.MockTransport(handler))
    tool = ToolDescriptor(
        name="calculator.evaluate", namespace="calculator", description="calc",
        input_schema={"type": "object", "properties": {"expression": {"type": "string"}}},
    )
    resp = await provider.complete(
        ModelRequest(purpose="assistant", messages=[{"role": "user", "content": "1+1"}],
                     tools=[tool.to_llm_schema()])
    )
    assert resp.tool_calls[0]["function"]["name"] == "calculator.evaluate"


@pytest.mark.asyncio
async def test_openai_health_check():
    provider = OpenAICompatibleProvider(
        api_key="sk-test", transport=_mock_transport({"data": []}, status=200)
    )
    assert await provider.health_check() is True
