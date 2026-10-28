"""Create persistent task and product-item tables.

Revision ID: 20261028_0001
Revises:
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261028_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("create_idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("create_request_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("create_idempotency_key"),
    )
    op.create_index("ix_tasks_updated_at", "tasks", ["updated_at"], unique=False)
    op.create_table(
        "product_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("page_title", sa.String(length=512), nullable=True),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("browser", sa.String(length=16), nullable=True),
        sa.Column("capture_status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_product_items_idempotency_key"),
    )
    op.create_index("ix_product_items_task_id", "product_items", ["task_id"], unique=False)
    op.create_index(
        "ix_product_items_task_created",
        "product_items",
        ["task_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_product_items_task_created", table_name="product_items")
    op.drop_index("ix_product_items_task_id", table_name="product_items")
    op.drop_table("product_items")
    op.drop_index("ix_tasks_updated_at", table_name="tasks")
    op.drop_table("tasks")
