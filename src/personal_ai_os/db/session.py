"""Async SQLAlchemy session factory.

Supports PostgreSQL (production) and SQLite (local dev / tests) via the
``DATABASE_URL`` setting. Tests call ``configure("sqlite+aiosqlite:///:memory:")``
before exercising the store.

In production ``DATABASE_URL`` must be set explicitly — there is no default
that contains guessable credentials.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool

from .models import Base

_MISSING_URL_MSG = "APP_ENV=production requires an explicit DATABASE_URL (the SQLite fallback is development-only)"


def get_database_url() -> str:
    """Return the database URL from the environment.

    Production requires an explicit URL. Development falls back to a local
    SQLite database.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise RuntimeError(_MISSING_URL_MSG)
    data_dir = os.path.join(os.getcwd(), os.getenv("PERSONAL_AI_DB_DIR", "data"))
    os.makedirs(data_dir, exist_ok=True)
    db_path = os.path.join(data_dir, os.getenv("PERSONAL_AI_DB_FILE", "app.db"))
    url = f"sqlite+aiosqlite:///{db_path}"
    return url


def _make_engine(url: str) -> AsyncEngine:
    if url.startswith("sqlite"):
        # :memory: uses StaticPool (single shared connection across sessions).
        # File-backed SQLite uses NullPool (new connection per session) — safer
        # under concurrency and avoids "database is locked" errors.
        poolclass = StaticPool if ":memory:" in url else NullPool
        engine = create_async_engine(
            url,
            poolclass=poolclass,
            connect_args={"check_same_thread": False} if ":memory:" in url else {},
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # P2-003: WAL + busy timeout make SQLite tolerable for dev
            # concurrency (single-process only; prod must use Postgres).
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        return engine
    return create_async_engine(url, pool_pre_ping=True)


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def configure(url: str) -> None:
    """Explicitly (re)configure the global engine. Tests use this to point at SQLite.

    Any previously configured engine's sync pool is disposed before re-wiring
    so connection pools and file handles are released.
    """
    global _engine, _session_factory
    if _engine is not None:
        _engine.sync_engine.dispose()
    _engine = _make_engine(url)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        configure(get_database_url())
    assert _engine is not None
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        configure(get_database_url())
    assert _session_factory is not None
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yield a session with automatic commit/rollback on exit."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables (dev convenience; production uses Alembic)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def reset_database() -> None:
    """Drop and recreate all tables on the current global engine (used by tests)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
