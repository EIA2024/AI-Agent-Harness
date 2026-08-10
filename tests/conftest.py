"""Shared test configuration and fixtures.

- Puts the repo root and ``src/`` on ``sys.path`` so both ``apps`` and
  ``personal_ai_os`` are importable regardless of how pytest is launched.
- Points the global DB engine at a fresh in-memory SQLite per test.
- Provides an ``api`` factory that builds the app with injected services and
  runs it in-process via ``httpx`` ASGITransport (single event loop, so async
  SQLAlchemy never crosses loops — unlike starlette's thread-based TestClient).
"""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
for _path in (ROOT, SRC):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from personal_ai_os.db import session as db_session  # noqa: E402


@pytest.fixture(autouse=True)
def _configure_db():
    """Point the global engine at a fresh in-memory SQLite before each test."""
    db_session.configure("sqlite+aiosqlite:///:memory:")
    yield


async def _dispose_engine() -> None:
    """Close any pooled aiosqlite connections while the test loop is still open."""
    engine = db_session._engine
    if engine is not None:
        try:
            await engine.dispose()
        except Exception:  # noqa: BLE001
            pass
    db_session._engine = None
    db_session._session_factory = None


@pytest.fixture
async def db():
    """Create tables on the global in-memory engine.

    Request this in async tests that touch the DB directly (outside the app).
    Disposes the engine on teardown so aiosqlite worker threads don't outlive
    the per-test event loop.
    """
    await db_session.init_db()
    yield
    await _dispose_engine()


@pytest.fixture
def make_api():
    """Factory fixture: run an app with injected services via ASGITransport.

    Usage::

        async with make_api(services=ServiceContainer(runner=FakeRunner())) as ac:
            r = await ac.get("/v1/tools")
    """
    from contextlib import asynccontextmanager

    from httpx import ASGITransport, AsyncClient

    from apps.api.main import create_app

    @asynccontextmanager
    async def _make(services=None):
        app = create_app(services=services)
        # Enter the app lifespan in THIS loop so DB setup stays loop-local.
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                yield ac
        await _dispose_engine()

    return _make
