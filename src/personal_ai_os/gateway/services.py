"""ServiceContainer — the dependency-injection root for the gateway/API layer.

Sibling departments (agent_runtime, tool_broker, memory_engine, ...) are under
parallel development and may not exist in this worktree yet. Every real
implementation is imported *lazily* and wrapped in try/except, so an
unavailable module simply leaves its slot ``None`` instead of breaking the app.
Tests inject mocks into a :class:`ServiceContainer` without importing any real
department code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ServiceContainer:
    """All injectable cross-department services. ``None`` = not wired up."""

    router: Any = None
    event_bus: Any = None
    model_provider: Any = None
    tool_registry: Any = None
    tool_broker: Any = None
    policy_engine: Any = None
    approval_engine: Any = None
    memory_store: Any = None
    context_engine: Any = None
    runner: Any = None
    scheduler: Any = None
    audit_logger: Any = None


def _lazy(factory):
    """Call ``factory()`` and swallow import/runtime errors -> ``None``."""
    try:
        return factory()
    except Exception as exc:  # noqa: BLE001 - a missing sibling department is expected
        logger.warning("Service %s unavailable: %s", getattr(factory, "__name__", factory), exc)
        return None


def build_default_services() -> ServiceContainer:
    """Build the production container using lazy imports.

    Each sibling department is imported inside its own try/except so a module
    that does not exist yet (parallel worktree) degrades to ``None``.
    """
    from personal_ai_os.gateway.router import SessionRouter

    container = ServiceContainer(router=SessionRouter())

    def _runner():
        from personal_ai_os.agent_runtime import Runner  # type: ignore[import-not-found]
        return Runner()

    container.runner = _lazy(_runner)

    def _event_bus():
        from personal_ai_os.scheduler import InMemoryEventBus  # type: ignore[import-not-found]
        return InMemoryEventBus()

    container.event_bus = _lazy(_event_bus)

    def _model_provider():
        from personal_ai_os.model_gateway import ModelClient  # type: ignore[import-not-found]
        return ModelClient()

    container.model_provider = _lazy(_model_provider)

    def _tool_registry():
        from personal_ai_os.tool_broker import ToolRegistry  # type: ignore[import-not-found]
        return ToolRegistry()

    container.tool_registry = _lazy(_tool_registry)

    def _tool_broker():
        from personal_ai_os.tool_broker import ToolBroker  # type: ignore[import-not-found]
        return ToolBroker(registry=container.tool_registry) if container.tool_registry else None

    container.tool_broker = _lazy(_tool_broker)

    def _policy_engine():
        from personal_ai_os.policy_engine import PolicyEngine  # type: ignore[import-not-found]
        return PolicyEngine()

    container.policy_engine = _lazy(_policy_engine)

    def _approval_engine():
        from personal_ai_os.policy_engine import ApprovalEngine  # type: ignore[import-not-found]
        return ApprovalEngine()

    container.approval_engine = _lazy(_approval_engine)

    def _memory_store():
        from personal_ai_os.memory_engine import MemoryStore  # type: ignore[import-not-found]
        return MemoryStore()

    container.memory_store = _lazy(_memory_store)

    def _context_engine():
        from personal_ai_os.context_engine import ContextEngine  # type: ignore[import-not-found]
        return ContextEngine()

    container.context_engine = _lazy(_context_engine)

    def _scheduler():
        from personal_ai_os.scheduler import SchedulerService  # type: ignore[import-not-found]
        return SchedulerService()

    container.scheduler = _lazy(_scheduler)

    def _audit_logger():
        from personal_ai_os.observability import AuditLogger  # type: ignore[import-not-found]
        return AuditLogger()

    container.audit_logger = _lazy(_audit_logger)

    return container
