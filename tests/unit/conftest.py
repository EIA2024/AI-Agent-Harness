"""Shared pytest fixtures for the Runtime & Context department tests.

Every test gets a fresh SQLite in-memory database (configured + reset).
Tests that need parent rows (User / Session) use the ``seeded_db`` fixture.
"""

from __future__ import annotations

import uuid

import pytest

from personal_ai_os.db import session as db_session
from personal_ai_os.db.models import Session, User

DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
async def _fresh_db():
    """Configure an in-memory DB and recreate all tables for each test."""
    db_session.configure(DB_URL)
    await db_session.reset_database()
    yield
    # close the engine while this test's event loop is still alive, so the
    # aiosqlite worker thread doesn't outlive the loop.
    await db_session.dispose_engine()


@pytest.fixture
async def seeded_db():
    """Insert a User + Session row and return their ids."""
    async with db_session.session_scope() as session:
        user = User(username=f"user-{uuid.uuid4().hex[:8]}")
        session.add(user)
        await session.flush()
        sess = Session(owner_id=user.id)
        session.add(sess)
        await session.flush()
        return user.id, sess.id
