"""adversarial integrity schema

Revision ID: a2c1f4e7d9b0
Revises: 997d7d9eed7e
Create Date: 2026-08-13

Closes schema drift discovered by the second adversarial review: hashed API-key
storage, durable message/event sequence identities, full active-run/session
constraints, and the event log table required by SSE replay.
"""

from alembic import op
import sqlalchemy as sa


revision = "a2c1f4e7d9b0"
down_revision = "997d7d9eed7e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("api_key_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_users_api_key_hash", "users", ["api_key_hash"], unique=False)

    op.add_column("messages", sa.Column("run_seq", sa.Integer(), nullable=True))
    op.create_index(
        "uq_message_run_seq", "messages", ["run_id", "run_seq"], unique=True
    )

    op.create_index("ix_runs_lease_owner", "runs", ["lease_owner"], unique=False)
    op.create_index(
        "ix_runs_lease_expires_at", "runs", ["lease_expires_at"], unique=False
    )

    op.drop_index("uq_run_one_active_per_session", table_name="runs")
    op.create_index(
        "uq_run_one_active_per_session",
        "runs",
        ["session_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('running', 'waiting_approval')"),
        postgresql_where=sa.text("status IN ('running', 'waiting_approval')"),
    )

    op.create_index(
        "uq_session_active_external_conversation",
        "sessions",
        ["owner_id", "channel", "external_conversation_id"],
        unique=True,
        sqlite_where=sa.text(
            "status = 'active' AND external_conversation_id IS NOT NULL"
        ),
        postgresql_where=sa.text(
            "status = 'active' AND external_conversation_id IS NOT NULL"
        ),
    )

    op.create_table(
        "event_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_log_run_id", "event_log", ["run_id"], unique=False)
    op.create_index(
        "uq_event_log_run_seq", "event_log", ["run_id", "seq"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_event_log_run_seq", table_name="event_log")
    op.drop_index("ix_event_log_run_id", table_name="event_log")
    op.drop_table("event_log")

    op.drop_index("uq_session_active_external_conversation", table_name="sessions")

    op.drop_index("uq_run_one_active_per_session", table_name="runs")
    op.create_index(
        "uq_run_one_active_per_session",
        "runs",
        ["session_id"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
        postgresql_where=sa.text("status = 'running'"),
    )

    op.drop_index("ix_runs_lease_expires_at", table_name="runs")
    op.drop_index("ix_runs_lease_owner", table_name="runs")

    op.drop_index("uq_message_run_seq", table_name="messages")
    op.drop_column("messages", "run_seq")

    op.drop_index("ix_users_api_key_hash", table_name="users")
    op.drop_column("users", "api_key_hash")
