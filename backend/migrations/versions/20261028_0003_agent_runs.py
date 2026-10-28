"""Persist shopping research runs and their tool observations."""

import sqlalchemy as sa
from alembic import op

revision = "20261028_0003"
down_revision = "20261028_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("question", sa.JSON(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_runs_task_id", "agent_runs", ["task_id"])
    op.create_index("ix_agent_runs_task_created", "agent_runs", ["task_id", "created_at"])
    op.create_index(
        "uq_agent_runs_active_task",
        "agent_runs",
        ["task_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued','running','needs_input','interrupted')"),
    )


def downgrade() -> None:
    op.drop_table("agent_runs")
