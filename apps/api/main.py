"""Personal AI OS — FastAPI application entrypoint.

``create_app`` is a dependency-injection factory: tests pass a
:class:`~personal_ai_os.gateway.services.ServiceContainer` full of mocks and
never trigger real sibling-department imports.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from personal_ai_os.db import session as db_session
from personal_ai_os.gateway.services import (
    ServiceContainer,
    build_default_services,
    complete_wiring,
)

from .deps import ensure_dev_owner
from .routers import (
    approvals,
    audit,
    automations,
    memories,
    messages,
    provider,
    runs,
    sessions,
    stream,
    tools,
)


async def ensure_database() -> None:
    """Configure the database and validate its production schema.

    P1-024: the SQLite fallback is DEV-ONLY — when ``APP_ENV=production`` a
    missing ``DATABASE_URL`` is a hard startup error, never a silent fallback.
    """
    app_env = os.environ.get("APP_ENV", "development").lower()
    database_url = db_session.get_database_url()
    if db_session._engine is None:
        db_session.configure(database_url)
    if app_env == "production":
        await _validate_schema_revision()
    else:
        # create_all is a development convenience only. Production deploys
        # migrate before startup and must already be at the Alembic head.
        await db_session.init_db()


async def _validate_schema_revision() -> None:
    """Fail production startup when the database is not at Alembic head."""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    expected = set(ScriptDirectory.from_config(config).get_heads())

    async with db_session.get_engine().connect() as connection:
        current = set(
            await connection.run_sync(
                lambda sync_connection: MigrationContext.configure(
                    sync_connection
                ).get_current_heads()
            )
        )
    if current != expected:
        raise RuntimeError(
            "database schema is not at Alembic head "
            f"(current={sorted(current)}, expected={sorted(expected)})"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _warn_insecure_config()
    await ensure_database()
    await ensure_dev_owner()
    # Async wiring: register connector tools, build the graph + runner.
    await complete_wiring(app.state.services)
    try:
        yield
    finally:
        from personal_ai_os.gateway.services import close_checkpointers

        await close_checkpointers()


# Weak dev values that must never reach production (P0-006).
_WEAK_DEV_KEYS = {"dev-key", "changeme", "password", "secret", "test"}
_WEAK_DB_PASSWORD_PATTERNS = (":pass@", ":password@", ":secret@", ":dev@", ":test@")


def _warn_insecure_config() -> None:
    """Log startup warnings when the configuration looks insecure (P0-006)."""
    import logging
    import os

    logger = logging.getLogger("personal_ai_os.config")
    dev_key = os.environ.get("PERSONAL_AI_DEV_API_KEY")
    if dev_key and dev_key.lower() in _WEAK_DEV_KEYS:
        logger.warning(
            "PERSONAL_AI_DEV_API_KEY is set to a well-known weak value %r — "
            "do not deploy this to a shared environment",
            dev_key,
        )
    db_url = os.environ.get("DATABASE_URL", "")
    if any(pattern in db_url.lower() for pattern in _WEAK_DB_PASSWORD_PATTERNS):
        logger.warning(
            "DATABASE_URL appears to embed a placeholder password — set a real "
            "credential (P0-006)"
        )
    if not dev_key:
        logger.info(
            "No PERSONAL_AI_DEV_API_KEY set — dev owner is disabled (auth requires "
            "a user with a configured key)"
        )


def healthz() -> dict:
    """Liveness: the process is up (not a readiness check, P1-023)."""
    return {"status": "ok", "service": "personal-ai-os-api"}


async def readyz(request: Request):  # noqa: ANN201
    """Readiness: the process can actually serve — DB, runner, registry, auth."""
    import logging

    from personal_ai_os.db.session import session_scope

    logger = logging.getLogger("personal_ai_os.ready")
    container = request.app.state.services
    checks: dict[str, bool] = {}
    try:
        async with session_scope() as s:
            from sqlalchemy import text

            await s.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("readyz database check failed: %s", exc)
        checks["database"] = False

    checks["runner"] = container.runner is not None
    checks["tool_registry"] = container.tool_registry is not None
    checks["model_provider"] = container.model_provider is not None
    ready = all(checks.values())
    payload = {"status": "ready" if ready else "not_ready", "checks": checks}
    return payload if ready else JSONResponse(status_code=503, content=payload)


class _BodyLimitMiddleware:
    """Count actual ASGI body bytes, including chunked requests."""

    def __init__(self, app, *, max_bytes: int):  # noqa: ANN001
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length", b"").decode("ascii", "ignore")
        if raw_length.isdigit() and int(raw_length) > self.max_bytes:
            await JSONResponse(
                status_code=413, content={"detail": "request body too large"}
            )(scope, receive, send)
            return

        received = 0
        messages: list[dict] = []
        while True:
            message = await receive()
            messages.append(message)
            if message.get("type") != "http.request":
                break
            received += len(message.get("body", b""))
            if received > self.max_bytes:
                await JSONResponse(
                    status_code=413, content={"detail": "request body too large"}
                )(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def replay_receive():
            if messages:
                return messages.pop(0)
            # Streaming responses keep listening for ``http.disconnect`` after
            # the request body is consumed. Returning an immediate empty body
            # forever creates a busy loop and prevents SSE responses closing.
            return await receive()

        await self.app(scope, replay_receive, send)


def create_app(services: ServiceContainer | None = None) -> FastAPI:
    """Build the app. ``services`` defaults to a lazily-built container."""
    container = services if services is not None else build_default_services()

    app = FastAPI(
        title="Personal AI OS API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.services = container

    # P1-022: CORS is OFF by default. Enable only an explicit allowlist via
    # PERSONAL_AI_CORS_ORIGINS (comma-separated); credentials are only sent when
    # an explicit origin (not "*") is allowed.
    cors_origins = [
        o.strip()
        for o in os.environ.get("PERSONAL_AI_CORS_ORIGINS", "").split(",")
        if o.strip()
    ]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Reject both declared and chunked oversized bodies before endpoint parsing.
    max_request_body = int(
        os.environ.get("PERSONAL_AI_MAX_BODY_BYTES", "2_000_000")
    )
    app.add_middleware(_BodyLimitMiddleware, max_bytes=max_request_body)

    app.include_router(sessions.router)
    app.include_router(messages.router)
    app.include_router(runs.router)
    app.include_router(stream.router)
    app.include_router(memories.router)
    app.include_router(tools.router)
    app.include_router(approvals.router)
    app.include_router(automations.router)
    app.include_router(audit.router)
    app.include_router(provider.router)

    app.add_api_route("/healthz", healthz, methods=["GET"], tags=["system"])
    app.add_api_route("/readyz", readyz, methods=["GET"], tags=["system"])

    _register_approval_error_handlers(app)

    return app


def _register_approval_error_handlers(app: FastAPI) -> None:
    """Map typed ApprovalEngine failures to HTTP statuses (P0-004).

    Without these the API would let an approval-resolution failure (expired,
    double-resolve, missing) escape as an opaque 500 — or, worse, be swallowed
    into a fake success. Expired → 410, double-resolve → 409, missing → 404,
    bad decision → 422.
    """
    from fastapi.responses import JSONResponse

    from personal_ai_os.policy_engine.approval import (
        ApprovalExpiredError,
        ApprovalInvalidDecisionError,
        ApprovalNotFoundError,
        ApprovalNotPendingError,
    )

    @app.exception_handler(ApprovalNotFoundError)
    async def _not_found(request: Request, exc: ApprovalNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ApprovalNotPendingError)
    async def _not_pending(request: Request, exc: ApprovalNotPendingError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ApprovalExpiredError)
    async def _expired(request: Request, exc: ApprovalExpiredError) -> JSONResponse:
        return JSONResponse(status_code=410, content={"detail": str(exc)})

    @app.exception_handler(ApprovalInvalidDecisionError)
    async def _invalid_decision(request: Request, exc: ApprovalInvalidDecisionError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})


def run() -> None:
    """Dev server entrypoint: ``python -m apps.api.main``."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = os.path.join(root, "src")
    for path in (root, src):
        if path not in sys.path:
            sys.path.insert(0, path)

    import uvicorn

    uvicorn.run(
        "apps.api.main:create_app",
        factory=True,
        host=os.environ.get("PERSONAL_AI_HOST", "0.0.0.0"),
        port=int(os.environ.get("PERSONAL_AI_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    run()
