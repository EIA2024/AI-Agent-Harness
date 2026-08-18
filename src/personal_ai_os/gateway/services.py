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
_CHECKPOINTER_CONTEXTS: list[Any] = []


def _production() -> bool:
    return os.environ.get("APP_ENV", "development").lower() == "production"


async def has_active_runs() -> bool:
    """Return whether any process-wide provider consumer is still active."""
    from sqlalchemy import exists, select

    from personal_ai_os.agent_runtime.runner import ACTIVE_RUN_STATUSES
    from personal_ai_os.db.models import Run
    from personal_ai_os.db.session import session_scope

    async with session_scope() as session:
        query = select(exists().where(Run.status.in_(ACTIVE_RUN_STATUSES)))
        return bool((await session.execute(query)).scalar())


@dataclass
class ServiceContainer:
    """All injectable cross-department services. ``None`` = not wired up."""

    router: Any = None
    event_bus: Any = None
    model_provider: Any = None
    provider_runtime: Any = None
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
        if _production():
            raise
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

    def _provider_runtime():
        from personal_ai_os.model_gateway import ProviderRuntimeService

        return ProviderRuntimeService(active_run_checker=has_active_runs)

    container.provider_runtime = _lazy(_provider_runtime)
    if container.provider_runtime is not None:
        container.model_provider = container.provider_runtime.provider

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
            if _production():
                raise
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
            if _production():
                raise
            logger.warning("Runner wiring failed: %s", exc)

    if _production() and container.runner is None:
        raise RuntimeError("production service wiring is incomplete: runner unavailable")

    return container


async def _open_persistent_checkpointer():
    """Open a durable checkpointer appropriate for the deployment mode.

    The saver stays open for the process lifetime (LangGraph async saver);
    its ``__aexit__`` would close the connection, so we enter it explicitly.
    """
    if _production():
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url.startswith(("postgresql://", "postgresql+asyncpg://")):
            raise RuntimeError("production checkpointer requires PostgreSQL DATABASE_URL")
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        psycopg_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        checkpointer_cm = AsyncPostgresSaver.from_conn_string(psycopg_url)
        saver = await checkpointer_cm.__aenter__()
        await saver.setup()
        _CHECKPOINTER_CONTEXTS.append(checkpointer_cm)
        logger.info("persistent checkpointer: PostgreSQL")
        return saver

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    data_dir = os.environ.get(
        "PERSONAL_AI_DATA_DIR", os.path.join(os.path.expanduser("~"), ".personal_ai")
    )
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "checkpoints.sqlite")
    checkpointer_cm = AsyncSqliteSaver.from_conn_string(path)
    saver = await checkpointer_cm.__aenter__()
    _CHECKPOINTER_CONTEXTS.append(checkpointer_cm)
    logger.info("persistent checkpointer: %s", path)
    return saver


async def close_checkpointers() -> None:
    """Close process-lifetime checkpointer contexts during app shutdown."""
    while _CHECKPOINTER_CONTEXTS:
        context = _CHECKPOINTER_CONTEXTS.pop()
        await context.__aexit__(None, None, None)
