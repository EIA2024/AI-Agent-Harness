"""Personal AI OS — FastAPI application entrypoint.

``create_app`` is a dependency-injection factory: tests pass a
:class:`~personal_ai_os.gateway.services.ServiceContainer` full of mocks and
never trigger real sibling-department imports.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from personal_ai_os.db import session as db_session
from personal_ai_os.gateway.services import (
    ServiceContainer,
    build_default_services,
    complete_wiring,
)

from . import config
from .deps import ensure_dev_owner
from .routers import (
    approvals,
    audit,
    automations,
    memories,
    messages,
    runs,
    sessions,
    stream,
    tools,
)


async def ensure_database() -> None:
    """Point the DB at a local SQLite file when nothing is configured yet."""
    if not os.environ.get("DATABASE_URL") and db_session._engine is None:
        data_dir = os.path.join(os.getcwd(), config.DB_DIR)
        os.makedirs(data_dir, exist_ok=True)
        db_path = os.path.join(data_dir, config.DB_FILE)
        db_session.configure(f"sqlite+aiosqlite:///{db_path}")
    # create_all is idempotent and doubles as a dev convenience.
    await db_session.init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _warn_insecure_config()
    await ensure_database()
    await ensure_dev_owner()
    # Async wiring: register connector tools, build the graph + runner.
    await complete_wiring(app.state.services)
    yield


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


async def readyz(request: Request) -> dict:
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
    return {"status": "ready" if ready else "not_ready", "checks": checks}


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

    # P1-026: reject oversized request bodies up front (413).
    _MAX_REQUEST_BODY = int(os.environ.get("PERSONAL_AI_MAX_BODY_BYTES", "2_000_000"))

    @app.middleware("http")
    async def _limit_body_size(request: Request, call_next):  # noqa: ANN001
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > _MAX_REQUEST_BODY:
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=413, content={"detail": "request body too large"})
        return await call_next(request)

    app.include_router(sessions.router)
    app.include_router(messages.router)
    app.include_router(runs.router)
    app.include_router(stream.router)
    app.include_router(memories.router)
    app.include_router(tools.router)
    app.include_router(approvals.router)
    app.include_router(automations.router)
    app.include_router(audit.router)

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
