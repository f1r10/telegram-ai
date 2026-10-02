"""Align file hash uniqueness with the ORM model."""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_file_hash_index"
down_revision: str | None = "0004_ingestion_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("file_objects_sha256_key", "file_objects", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("file_objects_sha256_key", "file_objects", ["sha256"])
