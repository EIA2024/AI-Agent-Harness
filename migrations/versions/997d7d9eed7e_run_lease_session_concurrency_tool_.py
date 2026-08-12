"""run lease, session concurrency, tool idempotency

Revision ID: 997d7d9eed7e
Revises: 6c431435743f
Create Date: 2026-08-12 15:43:45.031181

The original revision accidentally contained SQLite-reflection noise that tried
to convert every UUID column from NUMERIC to UUID. Those ALTERs were never part
of the feature and are invalid/non-portable on SQLite. Keep this historical
revision id but limit it to the changes it actually introduced.
"""

from alembic import op
import sqlalchemy as sa


revision = "997d7d9eed7e"
down_revision = "6c431435743f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("lease_owner", sa.String(length=64), nullable=True))
    op.add_column(
        "runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "uq_run_one_active_per_session",
        "runs",
        ["session_id"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
        postgresql_where=sa.text("status = 'running'"),
    )
    # A unique index is portable to SQLite; ALTER TABLE ADD CONSTRAINT is not.
    op.create_index(
        "uq_toolcall_run_idem", "tool_calls", ["run_id", "idempotency_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_toolcall_run_idem", table_name="tool_calls")
    op.drop_index("uq_run_one_active_per_session", table_name="runs")
    op.drop_column("runs", "lease_expires_at")
    op.drop_column("runs", "lease_owner")
