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
    capabilities: Any = None
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
        # P1-014: owner-aware tool visibility (deny-sets plug in here).
        from personal_ai_os.gateway.capabilities import CapabilityService

        container.capabilities = CapabilityService(container.tool_registry)
        return ToolBroker(
            registry=container.tool_registry,
            policy_engine=container.policy_engine,
            credential_broker=container.credential_broker,
            connectors={c.connector_name: c for c in connectors},
            event_bus=container.event_bus,
            approval_engine=container.approval_engine,
            audit_logger=container.audit_logger,
            capabilities=container.capabilities,
        )

    container.tool_broker = _lazy(_tool_broker)

    # -- model gateway ------------------------------------------------------

    def _model_provider():
        from personal_ai_os.model_gateway import (
            EchoProvider,
            ModelRouter,
            ProviderConfigStore,
        )

        profile = ProviderConfigStore().get_active()

        if profile is not None:
            if profile.format == "openai":
                from personal_ai_os.model_gateway import OpenAICompatibleProvider

                provider = OpenAICompatibleProvider(
                    api_key=profile.api_key,
                    base_url=profile.base_url or "https://api.openai.com/v1",
                    default_model=profile.model or "gpt-4o-mini",
                    default_max_tokens=profile.max_tokens or 4096,
                )
                logger.info(
                    "Using provider profile %r (%s, %s)",
                    profile.name, profile.format, provider.base_url,
                )
            elif profile.format == "anthropic":
                from personal_ai_os.model_gateway import AnthropicProvider

                provider = AnthropicProvider(
                    api_key=profile.api_key,
                    default_model=profile.model or "claude-haiku-4-5",
                )
                logger.info("Using provider profile %r (anthropic)", profile.name)
            else:
                return EchoProvider()
            # Route every purpose to the profile's single model.
            model = getattr(provider, "default_model", "default")
            config = {
                "roles": {
                    "router": [model],
                    "worker": [model],
                    "vision": [model],
                    "embedding": [],
                }
            }
            return ModelRouter(providers={"primary": provider}, config=config)

        # No profile configured → fall back to env key, else demo echo.
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            from personal_ai_os.model_gateway import AnthropicProvider

            return ModelRouter(providers={"anthropic": AnthropicProvider(api_key=api_key)})
        logger.warning(
            "No LLM provider profile configured (run `personal-ai config init`); "
            "using EchoProvider demo mode."
        )
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

    # The runner (and connector registration) requires async work, so it is
    # built in complete_wiring() during app startup.

    return container


async def complete_wiring(container: ServiceContainer) -> ServiceContainer:
    """Finish the async part of wiring: register connector tools into the
    broker's registry, then build the graph + runner.

    Called from the API lifespan. Idempotent-safe: a container already wired
    (runner not None) is returned as-is.
    """
    if container.runner is not None:
        return container

    if container.tool_broker is not None:
        from connectors import get_builtin_connectors

        try:
            for connector in get_builtin_connectors():
                await container.tool_broker.register_connector(connector)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Connector registration failed: %s", exc)

    if (
        container.model_provider is not None
        and container.tool_broker is not None
        and container.context_engine is not None
    ):
        try:
            from personal_ai_os.agent_runtime import RunRunner, build_graph

            graph = build_graph(
                context_engine=container.context_engine,
                model_provider=container.model_provider,
                tool_broker=container.tool_broker,
                memory_store=container.memory_store,
            )
            container.runner = RunRunner(
                graph=graph,
                model_provider=container.model_provider,
                tool_broker=container.tool_broker,
                memory_store=container.memory_store,
                context_engine=container.context_engine,
                policy_engine=container.policy_engine,
                approval_engine=container.approval_engine,
                event_bus=container.event_bus,
                # P0-005: a persistent checkpointer so approval/interrupt state
                # survives a server restart (never InMemorySaver in production).
                checkpointer=await _open_persistent_checkpointer(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Runner wiring failed: %s", exc)

    return container


async def _open_persistent_checkpointer():
    """Open a file-backed SQLite checkpointer at the data dir.

    The saver stays open for the process lifetime (LangGraph async saver);
    its ``__aexit__`` would close the connection, so we enter it explicitly.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    data_dir = os.environ.get(
        "PERSONAL_AI_DATA_DIR", os.path.join(os.path.expanduser("~"), ".personal_ai")
    )
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "checkpoints.sqlite")
    checkpointer_cm = AsyncSqliteSaver.from_conn_string(path)
    saver = await checkpointer_cm.__aenter__()
    logger.info("persistent checkpointer: %s", path)
    return saver
