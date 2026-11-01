"""Keep the extension's failure reason so a capture failure is diagnosable."""

import sqlalchemy as sa
from alembic import op

revision = "20261101_0005"
down_revision = "20261028_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A failed capture used to be indistinguishable from a lease timeout: both ended as
    # status=failed with only a code. The extension's own message now travels with it.
    op.add_column("browser_capture_jobs", sa.Column("error_detail", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("browser_capture_jobs", "error_detail")
