"""Preserve the filename used for each duplicate submission."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_submission_filename"
down_revision: str | None = "0005_file_hash_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_session_items",
        sa.Column("submitted_filename", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ingestion_session_items", "submitted_filename")
