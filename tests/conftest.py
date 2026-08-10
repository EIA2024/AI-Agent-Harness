"""Shared pytest fixtures."""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture(autouse=True)
async def _db(tmp_path):
    """Point the global async DB engine at an in-memory SQLite DB and reset it.

    Every test (including non-DB ones) gets a fresh schema. ``StaticPool`` keeps
    a single shared connection so all ``session_scope()`` calls inside one test
    see the same data.
    """
    from personal_ai_os.db import session

    await session.dispose_engine()
    session.configure("sqlite+aiosqlite:///:memory:")
    await session.reset_database()
    yield
    await session.dispose_engine()


@pytest.fixture
async def user_run():
    """Create a User + Run row and return (owner_id, run_id).

    SQLite runs with ``PRAGMA foreign_keys=ON``, so rows referencing ``users``
    / ``runs`` (memories, approvals) must point at real rows.
    """
    from personal_ai_os.db.models import Run, User
    from personal_ai_os.db.session import session_scope

    async with session_scope() as session:
        user = User(username=f"t_{uuid4().hex[:12]}", display_name="test")
        session.add(user)
        await session.flush()
        run = Run(owner_id=user.id, status="completed", input={})
        session.add(run)
        await session.flush()
        return user.id, run.id
