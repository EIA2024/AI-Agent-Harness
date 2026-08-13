"""Shared fakes for the Runtime & Context department unit tests.

These implement the cross-department protocols (``ModelProvider``,
``ToolBroker``, ``MemoryStore``, ``ContextEngine``, ``ToolRegistry``) as
deterministic doubles — no real connectors involved.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime

from personal_ai_os.common.models import (
    ApprovalRequiredError,
    Memory,
    MemoryQuery,
    ModelResponse,
    ToolDescriptor,
    ToolResult,
)

# ---------------------------------------------------------------------------
# Model provider
# ---------------------------------------------------------------------------


class FakeModelProvider:
    """Scripted provider: each ``complete`` call pops the next script entry.

    ``script`` is a list of dicts, each with optional ``content`` and
    ``tool_calls`` keys.
    """

    def __init__(self, script=None):
        self.script = list(script or [])
        self.i = 0
        self.calls = []  # every ModelRequest received, for assertions

    async def complete(self, request):
        self.calls.append(request)
        if self.i < len(self.script):
            entry = self.script[self.i]
        else:
            entry = {"content": None, "tool_calls": None}
        self.i += 1
        return ModelResponse(
            content=entry.get("content"),
            tool_calls=entry.get("tool_calls"),
            model="fake",
            provider="fake",
        )

    async def health_check(self):
        return True


def tool_call(name: str, arguments: dict, call_id: str | None = None) -> dict:
    """Build an LLM-style tool call dict."""
    import json

    return {
        "id": call_id or f"call-{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


# ---------------------------------------------------------------------------
# Tool broker
# ---------------------------------------------------------------------------


class FakeToolBroker:
    """Deterministic tool broker with an optional approval gate."""

    def __init__(self, tools=None, results=None, require_approval=()):
        self.tools = list(tools or [])
        self.results = list(results or [])
        self.require_approval = set(require_approval)
        self.calls = []  # (tool_name, arguments, ctx)
        self.denied = set()

    async def execute(
        self, tool_name: str, arguments: dict, context, *, before_connector=None
    ):
        self.calls.append((tool_name, dict(arguments), context))
        # A real broker keeps raising until an approved context is attached —
        # this is what makes the runtime's approval node replay-safe.
        if tool_name in self.require_approval and context.approval_id is None:
            raise ApprovalRequiredError(
                request_id=uuid.uuid4(),
                tool_name=tool_name,
                risk_level=2,
                reason="high-risk tool needs approval",
            )
        if before_connector is not None:
            callback_result = before_connector()
            if inspect.isawaitable(callback_result):
                await callback_result
        if self.results:
            idx = min(len(self.calls) - 1, len(self.results) - 1)
            result = self.results[idx]
        else:
            result = ToolResult.ok(data={}, text="fake result")
        return result

    async def list_available_tools(self, owner_id=None, query=None):
        return self.tools


def make_tool(name: str, description: str = "A test tool", risk_level: int = 0) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        namespace="test",
        description=description,
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        risk_level=risk_level,
    )


# ---------------------------------------------------------------------------
# Context engine
# ---------------------------------------------------------------------------


class FakeContextEngine:
    """Stub ContextEngine used by graph/runner tests (real one is tested separately)."""

    def __init__(self, system_prompt: str = "system prompt stub"):
        self.system_prompt = system_prompt
        self.build_count = 0

    async def build(self, state: dict, *, available_tokens: int | None = None) -> dict:
        self.build_count += 1
        user_input = state.get("user_input", "")
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_input},
        ]
        return {
            "messages": messages,
            "system_prompt": self.system_prompt,
            "token_count": 12,
            "sections": {"system_identity": self.system_prompt, "user_input": user_input},
            "context_items": [
                {"content": self.system_prompt, "source": "system",
                 "trust": "trusted_system", "type": "system_identity", "metadata": {}}
            ],
            "budget_usage": {},
        }


# ---------------------------------------------------------------------------
# Memory store
# ---------------------------------------------------------------------------


class FakeMemoryStore:
    def __init__(self, memories=None):
        self.memories = list(memories or [])
        self.written = []
        self.search_log = []

    async def search(self, query: MemoryQuery):
        self.search_log.append(query)
        return [m for m in self.memories if query.scope is None or m.scope == query.scope][: query.limit]

    async def write(self, memory):
        self.written.append(memory)
        return memory


def make_memory(content: str, scope: str = "global", score: float = 0.5, type_: str = "fact") -> Memory:
    return Memory(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        type=type_,
        scope=scope,
        content=content,
        summary=None,
        importance=0.5,
        confidence=0.8,
        source_type="conversation",
        source_id=None,
        sensitivity="personal",
        status="active",
        created_at=datetime.now(UTC),
        score=score,
    )


# ---------------------------------------------------------------------------
# Tool registry (used by ContextEngine)
# ---------------------------------------------------------------------------


class FakeToolRegistry:
    def __init__(self, tools=None):
        self.tools = list(tools or [])

    def list_tools(self):
        return self.tools


# ---------------------------------------------------------------------------
# Policy / approval engines (unused in MVP but injected into the runner)
# ---------------------------------------------------------------------------


class FakePolicyEngine:
    async def evaluate(self, tool, arguments, context):
        raise NotImplementedError  # tool broker handles policy in MVP


class FakeApprovalEngine:
    def __init__(self):
        self.resolved = []

    async def resolve(self, approval_id, *, decision, approved_by, edited_arguments=None):
        self.resolved.append((approval_id, decision, approved_by, edited_arguments))
        return {"status": "resolved"}
