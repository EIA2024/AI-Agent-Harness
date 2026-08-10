"""Personal AI OS — FastAPI application entrypoint.

``create_app`` is a dependency-injection factory: tests pass a
:class:`~personal_ai_os.gateway.services.ServiceContainer` full of mocks and
never trigger real sibling-department imports.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from personal_ai_os.db import session as db_session
from personal_ai_os.gateway.services import ServiceContainer, build_default_services, complete_wiring

from . import config
from .deps import ensure_dev_owner
from .routers import approvals, audit, automations, memories, messages, runs, sessions, stream, tools


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
    await ensure_database()
    await ensure_dev_owner()
    # Async wiring: register connector tools, build the graph + runner.
    await complete_wiring(app.state.services)
    yield


def healthz() -> dict:
    return {"status": "ok", "service": "personal-ai-os-api"}


def create_app(services: ServiceContainer | None = None) -> FastAPI:
    """Build the app. ``services`` defaults to a lazily-built container."""
    container = services if services is not None else build_default_services()

    app = FastAPI(
        title="Personal AI OS API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.services = container

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    return app


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
