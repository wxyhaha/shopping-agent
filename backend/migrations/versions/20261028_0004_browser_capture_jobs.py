"""Persist leased browser capture requests made by the research Agent."""

import sqlalchemy as sa
from alembic import op

revision = "20261028_0004"
down_revision = "20261028_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_capture_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("product_items.id", ondelete="SET NULL")),
        sa.Column("tool_call_id", sa.String(256), nullable=False),
        sa.Column("reason", sa.String(800), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("lease_hash", sa.String(64)),
        sa.Column("client_id", sa.String(36)),
        sa.Column("browser", sa.String(16)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "snapshot_id", sa.String(36), sa.ForeignKey("capture_snapshots.id", ondelete="SET NULL")
        ),
        sa.Column("error_code", sa.String(32)),
        sa.UniqueConstraint("run_id", "tool_call_id", name="uq_browser_capture_tool_call"),
    )
    op.create_index("ix_browser_capture_pending", "browser_capture_jobs", ["status", "expires_at"])


def downgrade() -> None:
    op.drop_table("browser_capture_jobs")
