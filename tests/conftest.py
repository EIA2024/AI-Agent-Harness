"""Shared pytest configuration and fixtures (merged from mpm + api + tools).

- Puts the repo root and ``src/`` on ``sys.path`` so both ``apps`` and
  ``personal_ai_os`` are importable regardless of how pytest is launched.
- Resets the global DB engine to a fresh in-memory SQLite schema before each
  test, so DB-touching tests (memory/approval/runner/tools) and API tests all
  get a clean database.
- Provides an ``api`` factory fixture that runs the app in-process via httpx
  ``ASGITransport`` (single event loop — compatible with async SQLAlchemy,
  unlike starlette's thread-based TestClient).
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

# Keep accidental db.session usage off PostgreSQL during tests.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
# API-key hashing is deliberately fail-closed when no deployment secret is
# configured.  Tests use an explicit, process-local key so auth fixtures drive
# the real HMAC path without depending on a developer's environment.
os.environ.setdefault("PERSONAL_AI_KEY_HASH_SECRET", "pytest-only-key-hash-secret")

from personal_ai_os.db import session as db_session  # noqa: E402


@pytest.fixture(autouse=True)
async def _fresh_db():
    """Point the global engine at a fresh in-memory SQLite schema per test.

    ``StaticPool`` keeps a single shared connection, so every
    ``session_scope()`` call inside one test sees the same data. The engine is
    disposed on teardown while the test's event loop is still open, so aiosqlite
    worker threads never outlive the loop.
    """
    await db_session.dispose_engine()
    db_session.configure("sqlite+aiosqlite:///:memory:")
    await db_session.reset_database()
    yield
    await db_session.dispose_engine()


async def _dispose_engine() -> None:
    """Close any pooled aiosqlite connections while the test loop is open."""
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
    """Request this in async tests that touch the DB directly (outside the app)."""
    yield
    await _dispose_engine()


@pytest.fixture
async def user_run():
    """Create a User + Run row and return (owner_id, run_id).

    SQLite runs with ``PRAGMA foreign_keys=ON``, so rows referencing ``users``
    / ``runs`` (memories, approvals) must point at real rows.
    """
    from personal_ai_os.db.models import Run, User
    from personal_ai_os.db.session import session_scope

    async with session_scope() as session:
        user = User(username=f"t_{os.urandom(4).hex()}", display_name="test")
        session.add(user)
        await session.flush()
        run = Run(owner_id=user.id, status="completed", input={})
        session.add(run)
        await session.flush()
        return user.id, run.id


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
