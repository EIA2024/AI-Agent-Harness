"""Protocol definitions (structural interfaces) for cross-department dependency injection.

Departments implement these Protocols and receive each other's implementations
via constructor injection — never via direct imports of concrete connectors.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
    Memory,
    MemoryCreate,
    MemoryQuery,
    ModelRequest,
    ModelResponse,
    PolicyDecision,
    ToolDescriptor,
    ToolExecutionContext,
    ToolResult,
)


@runtime_checkable
class ModelProvider(Protocol):
    """LLM provider abstraction. Implemented by model_gateway department."""

    provider_name: str

    async def complete(self, request: ModelRequest) -> ModelResponse:
        ...

    async def health_check(self) -> bool:
        ...


@runtime_checkable
class MemoryStore(Protocol):
    """Long-term memory persistence. Implemented by memory_engine department."""

    async def search(self, query: MemoryQuery) -> list[Memory]:
        ...

    async def write(self, memory: MemoryCreate) -> Memory:
        ...


@runtime_checkable
class PolicyEngine(Protocol):
    """Security policy evaluation. Implemented by policy_engine department."""

    async def evaluate(
        self,
        tool: ToolDescriptor,
        arguments: dict,
        context: ToolExecutionContext,
    ) -> PolicyDecision:
        ...


@runtime_checkable
class ApprovalEngine(Protocol):
    """Human-in-the-loop approval. Implemented by policy_engine department."""

    async def create_request(self, *, run_id, session_id, owner_id, action_summary,
                             tool_name, arguments_preview, risk_level,
                             risk_reason, requires_auth_method=None, expires_at=None) -> object:
        ...

    async def resolve(self, approval_id, *, decision: str, approved_by, edited_arguments=None) -> object:
        ...


@runtime_checkable
class CredentialBroker(Protocol):
    """Credential injection. Implemented by policy_engine department."""

    async def inject(self, tool: ToolDescriptor, arguments: dict, owner_id) -> dict:
        ...


@runtime_checkable
class Connector(Protocol):
    """A native connector exposing tools. Implemented by connectors/*."""

    connector_name: str

    async def list_tools(self) -> list[ToolDescriptor]:
        ...

    async def execute(self, tool: str, arguments: dict, ctx: ToolExecutionContext) -> ToolResult:
        ...


@runtime_checkable
class ContextEngine(Protocol):
    """Prompt assembly. Implemented by context_engine department."""

    async def build(self, state: dict, *, available_tokens: int | None = None) -> dict:
        """Return {"messages": [...], "system_prompt": str, "token_count": int, ...}"""
        ...


@runtime_checkable
class SkillEngine(Protocol):
    """Skill registry/search. Implemented by skill_engine department."""

    async def search(self, query: str, owner_id, limit: int = 5) -> list:
        ...


@runtime_checkable
class Scheduler(Protocol):
    """Automation/scheduling. Implemented by scheduler department."""

    async def create_automation(self, *, name: str, trigger_type: str, trigger_config: dict,
                                prompt: str, owner_id, notify_channel: str | None = None) -> object:
        ...


@runtime_checkable
class EventBus(Protocol):
    """Internal event bus. Implemented by scheduler department."""

    async def publish(self, event) -> None:
        ...

    def subscribe(self, event_type: str, handler) -> None:
        ...


@runtime_checkable
class ApprovalHandler(Protocol):
    """Callback interface used by the Runtime to notify of pending approvals."""

    async def on_approval_required(self, request) -> None:
        ...
