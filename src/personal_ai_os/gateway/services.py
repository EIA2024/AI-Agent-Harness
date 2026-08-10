"""ServiceContainer — the dependency-injection root for the gateway/API layer.

Real implementations of every department are wired here into a single
:class:`ServiceContainer`. Sibling departments that are not yet merged degrade
gracefully to ``None`` (with a warning) so the app still boots.
"""

from __future__ import annotations

import logging
import os
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
    credential_broker: Any = None
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
    """Build the production container, wiring real department implementations.

    Order matters: dependencies are constructed first, then dependents.
    Modules not yet present (e.g. a connector package still in flight) degrade
    to ``None`` rather than breaking startup.
    """
    from personal_ai_os.gateway.router import SessionRouter

    container = ServiceContainer(router=SessionRouter())

    # -- leaf services ------------------------------------------------------

    def _event_bus():
        from personal_ai_os.scheduler import EventBus
        return EventBus()

    container.event_bus = _lazy(_event_bus)

    def _policy_engine():
        from personal_ai_os.policy_engine import PolicyEngine
        return PolicyEngine()

    container.policy_engine = _lazy(_policy_engine)

    def _credential_broker():
        from personal_ai_os.policy_engine import CredentialBroker
        return CredentialBroker()

    container.credential_broker = _lazy(_credential_broker)

    def _approval_engine():
        from personal_ai_os.policy_engine import ApprovalEngine
        return ApprovalEngine()

    container.approval_engine = _lazy(_approval_engine)

    def _memory_store():
        from personal_ai_os.memory_engine import SQLMemoryStore
        from personal_ai_os.model_gateway import DeterministicEmbedding

        emb = DeterministicEmbedding()
        return SQLMemoryStore(embedding_provider=emb, event_bus=container.event_bus)

    container.memory_store = _lazy(_memory_store)

    def _audit_logger():
        from personal_ai_os.observability import AuditLogger
        return AuditLogger(event_bus=container.event_bus)

    container.audit_logger = _lazy(_audit_logger)

    # -- tool registry + broker (needs connectors package) ------------------

    def _tool_registry():
        from personal_ai_os.tool_broker import ToolRegistry
        return ToolRegistry()

    container.tool_registry = _lazy(_tool_registry)

    def _tool_broker():
        if container.tool_registry is None:
            return None
        from connectors import get_builtin_connectors
        from personal_ai_os.tool_broker import ToolBroker

        connectors = get_builtin_connectors()
        return ToolBroker(
            registry=container.tool_registry,
            policy_engine=container.policy_engine,
            credential_broker=container.credential_broker,
            connectors={c.connector_name: c for c in connectors},
            event_bus=container.event_bus,
        )

    container.tool_broker = _lazy(_tool_broker)

    # -- model gateway ------------------------------------------------------

    def _model_provider():
        from personal_ai_os.model_gateway import EchoProvider, ModelRouter

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            from personal_ai_os.model_gateway import AnthropicProvider
            return ModelRouter(providers={"anthropic": AnthropicProvider(api_key=api_key)})
        # No API key -> echo provider so the system runs end-to-end in demo mode.
        logger.info("ANTHROPIC_API_KEY not set; using EchoProvider (demo mode)")
        return EchoProvider()

    container.model_provider = _lazy(_model_provider)

    # -- context engine -----------------------------------------------------

    def _context_engine():
        if container.memory_store is None:
            return None
        from personal_ai_os.context_engine import ContextEngine
        return ContextEngine(memory_store=container.memory_store, tool_registry=container.tool_registry)

    container.context_engine = _lazy(_context_engine)

    # -- runner -------------------------------------------------------------

    def _runner():
        if container.model_provider is None or container.tool_broker is None or container.context_engine is None:
            return None
        from personal_ai_os.agent_runtime import RunRunner, build_graph

        graph = build_graph(
            context_engine=container.context_engine,
            model_provider=container.model_provider,
            tool_broker=container.tool_broker,
            memory_store=container.memory_store,
        )
        return RunRunner(
            graph=graph,
            model_provider=container.model_provider,
            tool_broker=container.tool_broker,
            memory_store=container.memory_store,
            context_engine=container.context_engine,
            policy_engine=container.policy_engine,
            approval_engine=container.approval_engine,
            event_bus=container.event_bus,
        )

    container.runner = _lazy(_runner)

    return container
