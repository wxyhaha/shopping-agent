"""Add immutable product capture snapshots.

Revision ID: 20261028_0002
Revises: 20261028_0001
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_0002"
down_revision: str | None = "20261028_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capture_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("adapter_id", sa.String(length=64), nullable=False),
        sa.Column("adapter_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("page_url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["product_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_capture_snapshots_idempotency_key"),
    )
    op.create_index("ix_capture_snapshots_item_id", "capture_snapshots", ["item_id"])
    op.create_index(
        "ix_capture_snapshots_item_received",
        "capture_snapshots",
        ["item_id", "received_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_capture_snapshots_item_received", table_name="capture_snapshots")
    op.drop_index("ix_capture_snapshots_item_id", table_name="capture_snapshots")
    op.drop_table("capture_snapshots")
