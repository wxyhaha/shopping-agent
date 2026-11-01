"""Track the follow-up detail-image read so a reader can wait for complete evidence."""

import sqlalchemy as sa
from alembic import op

revision = "20261101_0006"
down_revision = "20261101_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The extension can only wait ~20 s, while reading a detail section takes about a minute,
    # so the read runs after the snapshot is stored. This column records whether it finished.
    # Existing rows default to "done": their payload was written before this column existed.
    op.add_column(
        "capture_snapshots",
        sa.Column("detail_enrichment", sa.String(16), nullable=False, server_default="done"),
    )


def downgrade() -> None:
    op.drop_column("capture_snapshots", "detail_enrichment")
