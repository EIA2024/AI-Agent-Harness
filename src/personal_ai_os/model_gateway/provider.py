"""LLM provider adapters.

Implements the ``ModelProvider`` protocol (see ``common/protocols.py``) for:

* ``AnthropicProvider`` — real Anthropic Messages API adapter (``anthropic`` SDK).
* ``FakeProvider`` — scripted in-memory provider used by unit tests.
* ``EchoProvider`` — trivial echo provider for CLI/demo purposes.

Error normalisation: any upstream failure is surfaced as ``ModelError`` so the
``ModelRouter`` can run its failover logic uniformly.
"""

from __future__ import annotations

import os
import time
from typing import Any

import anthropic

from ..common.models import ModelError, ModelRequest, ModelResponse, ModelUsage

# ---------------------------------------------------------------------------
# Anthropic pricing (USD per 1M tokens, approximate). Used for cost tracking.
# ---------------------------------------------------------------------------
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cached": 0.1},
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0, "cached": 0.3},
    "claude-sonnet-5": {"input": 3.0, "output": 15.0, "cached": 0.3},
    "claude-opus-5": {"input": 15.0, "output": 75.0, "cached": 1.5},
}


def estimate_cost_usd(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    """Estimate the USD cost of a call given the per-model pricing table."""
    price = MODEL_PRICING.get(model)
    if price is None:
        return 0.0
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
            "max_tokens": request.max_tokens or 1024,
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
        output_tokens = int(usage.get("output_tokens", 0)) or (len((entry.get("content") or "")) // 4 or 1)
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
