"""Link follow-up research runs to their previous report."""

import sqlalchemy as sa
from alembic import op

revision = "20261105_0007"
down_revision = "20261101_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite cannot add a self-referencing constraint with ALTER TABLE. Check for the
    # column first because an earlier startup may have added it before failing at that step.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_runs")}
    if "parent_run_id" not in columns:
        op.add_column("agent_runs", sa.Column("parent_run_id", sa.String(36), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_runs")}
    if "parent_run_id" in columns:
        op.drop_column("agent_runs", "parent_run_id")
