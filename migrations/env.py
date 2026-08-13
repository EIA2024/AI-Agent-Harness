"""Alembic async environment for Personal AI OS.

Uses the SQLAlchemy metadata from `src/personal_ai_os/db/models.py`.
Requires a venv python:  python -m alembic upgrade head
"""

import asyncio
import os

from alembic import context
from sqlalchemy import Numeric, Uuid
from sqlalchemy.ext.asyncio import create_async_engine

from personal_ai_os.db.models import Base

config = context.config

DATABASE_URL = os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url"))


def _compare_type(
    migration_context,
    inspected_column,
    metadata_column,
    inspected_type,
    metadata_type,
) -> bool | None:
    """Ignore SQLite's lossy reflection of native UUID declarations.

    SQLite assigns unknown ``UUID`` declarations NUMERIC affinity, so Alembic
    otherwise proposes a destructive NUMERIC-to-UUID change for every key.
    PostgreSQL and all other real type differences retain Alembic's defaults.
    """
    del inspected_column, metadata_column
    if (
        migration_context.dialect.name == "sqlite"
        and isinstance(inspected_type, Numeric)
        and isinstance(metadata_type, Uuid)
    ):
        return False
    return None


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=_compare_type,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
