"""LLM provider adapters.

Implements the ``ModelProvider`` protocol (see ``common/protocols.py``) for:

* ``AnthropicProvider`` — real Anthropic Messages API adapter (``anthropic`` SDK).
* ``FakeProvider`` — scripted in-memory provider used by unit tests.
* ``EchoProvider`` — trivial echo provider for CLI/demo purposes.

Error normalisation: any upstream failure is surfaced as ``ModelError`` so the
``ModelRouter`` can run its failover logic uniformly.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import anthropic
import httpx

from ..common.models import ModelError, ModelRequest, ModelResponse, ModelStreamEvent, ModelUsage

# ---------------------------------------------------------------------------
# Anthropic pricing (USD per 1M tokens, approximate). Used for cost tracking.
# ---------------------------------------------------------------------------
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cached": 0.1},
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0, "cached": 0.3},
    "claude-sonnet-5": {"input": 3.0, "output": 15.0, "cached": 0.3},
    "claude-opus-5": {"input": 15.0, "output": 75.0, "cached": 1.5},
}


def estimate_cost_usd(
    model: str, input_tokens: int, cached_tokens: int, output_tokens: int
) -> float | None:
    """Estimate the USD cost of a call given the per-model pricing table."""
    price = MODEL_PRICING.get(model)
    if price is None:
        return None
    return (
        input_tokens * price["input"] + cached_tokens * price["cached"] + output_tokens * price["output"]
    ) / 1_000_000


# ---------------------------------------------------------------------------
# Message / tool conversion helpers (shared by adapters)
# ---------------------------------------------------------------------------


def split_system_and_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Split OpenAI-style messages into (system_prompt, non-system messages).

    System messages are concatenated into a single prompt string; the rest are
    kept in order and passed to the model adapter.
    """
    system_parts: list[str] = []
    rest: list[dict] = []
    for msg in messages or []:
        if msg.get("role") == "system":
            system_parts.append(str(msg.get("content", "")))
        else:
            rest.append(dict(msg))
    return "\n".join(p for p in system_parts if p), rest


def to_anthropic_tools(tools: list[dict] | None) -> list[dict] | None:
    """Convert tool schemas into the Anthropic ``tools`` parameter format.

    Accepts both OpenAI-style schemas (``{"type": "function", "function": {...}}``)
    and plain schemas (``{"name": ..., "description": ..., "input_schema": ...}``
    or ``{"name": ..., "description": ..., "parameters": ...}``).
    """
    if not tools:
        return None
    converted: list[dict] = []
    for tool in tools:
        fn = tool.get("function") if isinstance(tool, dict) and "function" in tool else tool
        if "input_schema" in fn:
            converted.append(dict(fn))
            continue
        schema = fn.get("input_schema") or fn.get("parameters") or {"type": "object"}
        converted.append(
            {
                "name": fn.get("name", "unknown_tool"),
                "description": fn.get("description", ""),
                "input_schema": schema,
            }
        )
    return converted or None


def to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert OpenAI-style messages to the Anthropic Messages API format.

    * ``system`` messages are dropped (extracted by ``split_system_and_messages``).
    * ``tool`` role messages become a ``user`` message containing a
      ``tool_result`` content block (bound to ``tool_use_id``).
    * consecutive same-role messages are merged, since Anthropic requires
      strictly alternating ``user`` / ``assistant`` turns.
    """
    out: list[dict] = []

    def _append(role: str, content: Any) -> None:
        if out and out[-1]["role"] == role:
            prev_content = out[-1]["content"]
            if isinstance(prev_content, str) and isinstance(content, str):
                out[-1]["content"] = prev_content + "\n" + content
            else:
                prev_blocks = [{"type": "text", "text": prev_content}] if isinstance(prev_content, str) else prev_content
                new_blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
                out[-1]["content"] = prev_blocks + new_blocks
        else:
            out.append({"role": role, "content": content})

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "user":
            _append("user", content)
        elif role == "assistant":
            blocks: list[dict] = []
            if isinstance(content, str):
                if content:
                    blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                blocks.extend(content)
            for tc in msg.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id") or f"toolu_{len(blocks) + 1:04d}",
                        "name": tc.get("name", "unknown_tool"),
                        "input": tc.get("arguments", {}) or {},
                    }
                )
            if blocks:
                _append("assistant", blocks)
        elif role == "tool":
            _append(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id") or msg.get("id") or "tool_use_0",
                        "content": content if isinstance(content, str) else str(content),
                    }
                ],
            )
    return out


def anthropic_finish_reason(stop_reason: str | None) -> str:
    """Map an Anthropic stop_reason to the shared finish_reason vocabulary."""
    if stop_reason == "tool_use":
        return "tool_calls"
    if stop_reason == "max_tokens":
        return "length"
    return "stop"


# ---------------------------------------------------------------------------
# AnthropicProvider
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """Real Anthropic Messages API adapter."""

    provider_name = "anthropic"

    #: Models this provider can serve (used by ``ModelRouter`` for selection).
    models: list[str] | None = [
        "claude-haiku-4-5",
        "claude-sonnet-4-5",
        "claude-sonnet-5",
        "claude-opus-5",
    ]

    def __init__(self, *, api_key: str | None = None, default_model: str = "claude-haiku-4-5"):
        self.api_key = api_key
        self.default_model = default_model
        self._client: anthropic.AsyncAnthropic | None = None

    # -- helpers -----------------------------------------------------------

    def _resolve_api_key(self) -> str:
        key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ModelError(
                "Anthropic API key is not configured. Set ANTHROPIC_API_KEY "
                "or pass api_key= to AnthropicProvider."
            )
        return key

    def _client_for(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self._resolve_api_key())
        return self._client

    def _model_for(self, request: ModelRequest) -> str:
        return request.preferred_model or self.default_model

    # -- ModelProvider protocol --------------------------------------------

    async def complete(self, request: ModelRequest) -> ModelResponse:
        model = self._model_for(request)
        system, messages = split_system_and_messages(request.messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens or 4096,
            "messages": to_anthropic_messages(messages),
        }
        if system:
            kwargs["system"] = system
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        tools = to_anthropic_tools(request.tools)
        if tools:
            kwargs["tools"] = tools

        started = time.monotonic()
        try:
            client = self._client_for()
            message = await client.messages.create(**kwargs)
        except ModelError:
            raise
        except Exception as exc:  # normalise upstream errors
            raise ModelError(f"Anthropic request failed: {exc}") from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in message.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    {"name": block.name, "arguments": dict(block.input or {}), "id": block.id}
                )

        usage = message.usage
        input_tokens = usage.input_tokens or 0
        cached_tokens = (usage.cache_read_input_tokens or 0) + (usage.cache_creation_input_tokens or 0)
        output_tokens = usage.output_tokens or 0

        return ModelResponse(
            content="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls or None,
            model=model,
            provider=self.provider_name,
            usage=ModelUsage(
                input_tokens=input_tokens,
                cached_tokens=cached_tokens,
                output_tokens=output_tokens,
                cost_usd=estimate_cost_usd(model, input_tokens, cached_tokens, output_tokens),
            ),
            finish_reason=anthropic_finish_reason(message.stop_reason),
            latency_ms=latency_ms,
        )

    async def health_check(self) -> bool:
        try:
            key = self._resolve_api_key()
        except ModelError:
            return False
        try:
            client = anthropic.AsyncAnthropic(api_key=key)
            await client.messages.create(
                model=self.default_model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        return list(self.models or [])


# ---------------------------------------------------------------------------
# OpenAICompatibleProvider
# ---------------------------------------------------------------------------


def _sanitize_tool_name(name: str) -> str:
    """Make a tool name acceptable to strict OpenAI-compatible endpoints.

    DeepSeek (and others) require tool names to match ``^[a-zA-Z0-9_-]+$`` —
    our namespaces use dots (``calculator.evaluate``). Replace every character
    outside the allowed set with ``_`` so the request passes schema validation.
    """
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in name)


def _disambiguate(base: str, original: str, name_map: dict) -> str:
    """Return a collision-free sanitized alias for ``original`` (P1-017)."""
    import hashlib

    candidate = f"{base[:32]}_{hashlib.sha256(original.encode()).hexdigest()[:6]}"
    n = 1
    while candidate in name_map and name_map[candidate] != original:
        candidate = f"{base[:24]}_{hashlib.sha256(f'{original}#{n}'.encode()).hexdigest()[:8]}"
        n += 1
    return candidate


def _sanitize_messages(messages: list[dict] | None) -> list[dict]:
    """Sanitize tool names inside message history for strict endpoints.

    DeepSeek rejects dotted tool names not just in the ``tools`` parameter but
    also in ``assistant`` tool_call frames and ``tool`` result frames that are
    replayed in later turns. Return a deep copy so the caller's history is
    never mutated.
    """
    import copy

    out: list[dict] = []
    for msg in messages or []:
        m = copy.deepcopy(msg)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                if isinstance(tc, dict):
                    fn = tc.get("function")
                    if isinstance(fn, dict) and fn.get("name"):
                        fn["name"] = _sanitize_tool_name(str(fn["name"]))
        if m.get("role") == "tool" and m.get("name"):
            m["name"] = _sanitize_tool_name(str(m["name"]))
        out.append(m)
    return out


class OpenAICompatibleProvider:
    """OpenAI-compatible ``/chat/completions`` adapter.

    Works with OpenAI, DeepSeek, Moonshot/Kimi, Zhipu GLM, Qwen, and any
    service exposing the OpenAI chat-completions schema. Uses plain ``httpx``
    so no extra SDK dependency is required.

    ``base_url`` is the API root — ``/chat/completions`` is appended.
    e.g. ``https://api.openai.com/v1`` or ``https://api.deepseek.com/v1``.
    ``request.messages`` and ``request.tools`` are already in OpenAI format
    (system/user/assistant/tool roles + function schemas), so they pass through
    as-is; tool calls come back in the native OpenAI shape, which the runtime's
    ``_extract_tool_call_fields`` already parses.
    """

    provider_name = "openai_compatible"

    #: wildcard — the model is chosen per profile, not from a fixed list
    models: list[str] | None = None

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        default_model: str = "gpt-4o-mini",
        reasoning_effort: str = "auto",
        default_max_tokens: int = 4096,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.reasoning_effort = reasoning_effort
        # Reasoning models (DeepSeek v4, Qwen, ...) count chain-of-thought
        # tokens against max_tokens; 1024 is far too small and truncates the
        # answer. 4096 leaves room for thinking + the actual reply.
        self.default_max_tokens = default_max_tokens
        self._transport = transport

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _build_payload(self, request: ModelRequest, *, stream: bool = False) -> tuple[dict, dict]:
        """Build the chat/completions payload plus a sanitized->original tool-name
        map for round-tripping. Shared by ``complete`` and ``stream``."""
        model = request.preferred_model or self.default_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": _sanitize_messages(request.messages),
            "max_tokens": request.max_tokens or self.default_max_tokens,
        }
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if self.reasoning_effort != "auto":
            payload["reasoning_effort"] = self.reasoning_effort

        # DeepSeek (and some other OpenAI-compatible endpoints) reject tool names
        # that are not ^[a-zA-Z0-9_-]+$ — our namespaces use dots ("calculator.evaluate").
        # Sanitize on the way out and map returned names back on the way in.
        name_map: dict[str, str] = {}
        if request.tools:
            payload["tools"] = []
            import copy

            for tool in request.tools:
                fn = tool.get("function", tool) if isinstance(tool, dict) else tool
                original = fn.get("name") if isinstance(fn, dict) else None
                if not original:
                    payload["tools"].append(tool)
                    continue
                sanitized = _sanitize_tool_name(original)
                # P1-017: disambiguate ANY collision — including an unchanged
                # valid name (web_get) colliding with a prior tool's sanitized
                # alias (web.get → web_get). Never silently overwrite the map.
                if sanitized in name_map and name_map[sanitized] != original:
                    sanitized = _disambiguate(sanitized, original, name_map)
                if sanitized == original:
                    name_map.setdefault(original, original)
                    payload["tools"].append(tool)
                else:
                    name_map[sanitized] = original
                    adjusted = copy.deepcopy(tool)
                    adjusted_fn = adjusted.get("function", adjusted) if isinstance(adjusted, dict) else adjusted
                    if isinstance(adjusted_fn, dict):
                        adjusted_fn["name"] = sanitized
                    payload["tools"].append(adjusted)
        if request.response_format:
            payload["response_format"] = request.response_format
        return payload, name_map

    @staticmethod
    def _map_tool_names(tool_calls: list[dict] | None, name_map: dict) -> list[dict] | None:
        if not tool_calls or not name_map:
            return tool_calls
        for tc in tool_calls:
            if isinstance(tc, dict):
                fn = tc.get("function")
                if isinstance(fn, dict) and fn.get("name") in name_map:
                    fn["name"] = name_map[fn["name"]]
        return tool_calls

    async def complete(self, request: ModelRequest) -> ModelResponse:
        model = request.preferred_model or self.default_model
        payload, name_map = self._build_payload(request, stream=False)

        started = time.monotonic()
        try:
            client_kwargs: dict[str, Any] = {"timeout": httpx.Timeout(120)}
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=self._headers()
                )
                if response.status_code >= 400:
                    # surface the provider's own error message (e.g. DeepSeek's
                    # schema/validation details) instead of a bare status code
                    body = response.text[:500]
                    raise ModelError(
                        f"OpenAI-compatible endpoint returned HTTP {response.status_code} "
                        f"for model {model!r}: {body}"
                    )
                response.raise_for_status()
                data = response.json()
        except ModelError:
            raise
        except Exception as exc:  # normalise upstream errors
            raise ModelError(f"OpenAI-compatible request failed: {exc}") from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        tool_calls = self._map_tool_names(message.get("tool_calls") or None, name_map)
        # DeepSeek reasoning models require `reasoning_content` to be passed back
        # verbatim when the assistant's tool-call frame is replayed next turn.
        # Stash it on the tool_call dict so the runtime can persist it.
        reasoning = message.get("reasoning_content")
        if reasoning and tool_calls:
            for tc in tool_calls:
                if isinstance(tc, dict):
                    tc["reasoning_content"] = reasoning

        usage_raw = data.get("usage") or {}
        cached_tokens = 0
        details = usage_raw.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached_tokens = int(details.get("cached_tokens", 0) or 0)

        finish = choice.get("finish_reason") or "stop"
        finish_map = {"tool_calls": "tool_calls", "length": "length", "stop": "stop"}
        finish_reason = finish_map.get(finish, "stop")

        return ModelResponse(
            content=content if content else None,
            tool_calls=tool_calls,
            model=model,
            provider=self.provider_name,
            usage=ModelUsage(
                input_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
                cached_tokens=cached_tokens,
                output_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
                # Pricing differs wildly per provider (OpenAI vs DeepSeek);
                # Unknown is distinct from a verified zero-cost call.
                cost_usd=None,
            ),
            finish_reason=finish_reason,
            latency_ms=latency_ms,
        )

    async def stream(self, request: ModelRequest):
        """Stream a chat completion as SSE deltas.

        Yields :class:`ModelStreamEvent` items: ``thinking_delta`` (reasoning
        content), ``text_delta``, one ``tool_call`` when tool use completes,
        then ``done``. Any non-2xx response raises :class:`ModelError`.
        """

        model = request.preferred_model or self.default_model
        payload, name_map = self._build_payload(request, stream=True)

        client_kwargs: dict[str, Any] = {"timeout": httpx.Timeout(300)}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_call_acc: dict[int, dict] = {}
        finish_reason = "stop"
        usage_raw: dict = {}

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/chat/completions", json=payload, headers=self._headers()
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise ModelError(
                            f"OpenAI-compatible endpoint returned HTTP {response.status_code} "
                            f"for model {model!r}: {body[:500]!r}"
                        )
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choice = (chunk.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}

                        reasoning = delta.get("reasoning_content")
                        if reasoning:
                            thinking_parts.append(reasoning)
                            yield ModelStreamEvent(type="thinking_delta", text=reasoning)

                        text = delta.get("content")
                        if text:
                            content_parts.append(text)
                            yield ModelStreamEvent(type="text_delta", text=text)

                        for tc in delta.get("tool_calls") or []:
                            idx = int(tc.get("index", 0))
                            acc = tool_call_acc.setdefault(idx, {"id": None, "function": {"name": None, "arguments": ""}})
                            if tc.get("id"):
                                acc["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                acc["function"]["name"] = fn["name"]
                            if fn.get("arguments"):
                                acc["function"]["arguments"] += fn.get("arguments") or ""

                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                        if chunk.get("usage"):
                            usage_raw = chunk["usage"]
        except ModelError:
            raise
        except Exception as exc:  # normalise upstream errors
            raise ModelError(f"OpenAI-compatible stream failed: {exc}") from exc

        tool_calls = None
        if tool_call_acc:
            tool_calls = [
                {
                    "id": acc["id"],
                    "type": "function",
                    "function": {"name": acc["function"]["name"], "arguments": acc["function"]["arguments"]},
                }
                for idx, acc in sorted(tool_call_acc.items())
            ]
            tool_calls = self._map_tool_names(tool_calls, name_map)
            reasoning = "".join(thinking_parts)
            if reasoning:
                for tc in tool_calls:
                    tc["reasoning_content"] = reasoning
            yield ModelStreamEvent(type="tool_call", tool_call=tool_calls)

        usage = ModelUsage(
            input_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
        )
        if finish_reason == "tool_calls":
            pass  # tool_call event already emitted above
        yield ModelStreamEvent(
            type="done",
            text="".join(content_parts),
            tool_call=tool_calls,
            usage=usage,
        )

    async def health_check(self) -> bool:
        try:
            client_kwargs: dict[str, Any] = {"timeout": httpx.Timeout(10)}
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.get(
                    f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"}
                )
                return response.status_code < 400
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        try:
            client_kwargs: dict[str, Any] = {"timeout": httpx.Timeout(10)}
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if response.status_code >= 400:
                    raise ModelError(
                        "OpenAI-compatible model enumeration failed "
                        f"with HTTP {response.status_code}"
                    )
                data = response.json()
        except ModelError:
            raise
        except Exception as exc:
            raise ModelError("OpenAI-compatible model enumeration failed") from exc
        models = {
            str(item["id"])
            for item in data.get("data", [])
            if isinstance(item, dict) and item.get("id")
        }
        return sorted(models)


# ---------------------------------------------------------------------------
# FakeProvider
# ---------------------------------------------------------------------------


class FakeProvider:
    """Scripted provider for tests. Returns items from ``script`` in order.

    Each script entry is a dict::

        {"content": str | None, "tool_calls": [{"name", "arguments"}] | None,
         "usage": {"input_tokens": ..., "output_tokens": ...} | None}

    When the script is exhausted the provider returns ``content=""``.
    """

    provider_name = "fake"

    def __init__(self, script: list[dict] | None = None, *, models: list[str] | None = None):
        self.script = list(script or [])
        self.i = 0
        self.models = models
        self.requests: list[ModelRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.i >= len(self.script):
            entry: dict = {"content": ""}  # exhausted -> empty content
        else:
            entry = self.script[self.i]
        self.i += 1
        usage = entry.get("usage") or {}
        model = request.preferred_model or "fake-model"
        input_tokens = int(usage.get("input_tokens", 0)) or max(1, len(request.messages) * 10)
        output_tokens = int(usage.get("output_tokens", 0)) or (len(entry.get("content") or "") // 4 or 1)
        cached_tokens = int(usage.get("cached_tokens", 0))
        return ModelResponse(
            content=entry.get("content"),
            tool_calls=entry.get("tool_calls"),
            model=model,
            provider=self.provider_name,
            usage=ModelUsage(
                input_tokens=input_tokens,
                cached_tokens=cached_tokens,
                output_tokens=output_tokens,
                cost_usd=estimate_cost_usd(model, input_tokens, cached_tokens, output_tokens),
            ),
            finish_reason="tool_calls" if entry.get("tool_calls") else "stop",
        )

    async def health_check(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# EchoProvider
# ---------------------------------------------------------------------------


class EchoProvider:
    """Trivial provider used for CLI demos — echoes the last user message."""

    provider_name = "echo"
    models: list[str] | None = None  # wildcard: serves any model

    def __init__(self, prefix: str = "Echo: "):
        self.prefix = prefix

    async def complete(self, request: ModelRequest) -> ModelResponse:
        last_user = ""
        for msg in request.messages:
            if msg.get("role") == "user":
                last_user = str(msg.get("content", ""))
        text = f"{self.prefix}{last_user}" if last_user else self.prefix.rstrip()
        return ModelResponse(
            content=text,
            tool_calls=None,
            model=request.preferred_model or "echo",
            provider=self.provider_name,
            usage=ModelUsage(input_tokens=0, output_tokens=max(1, len(text) // 4)),
            finish_reason="stop",
        )

    async def health_check(self) -> bool:
        return True

    async def list_models(self) -> list[str]:
        return []
