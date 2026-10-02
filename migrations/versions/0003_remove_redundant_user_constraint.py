"""Align Telegram identity uniqueness with the ORM model."""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_user_identity_index"
down_revision: str | None = "0002_users_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("users_telegram_user_id_key", "users", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("users_telegram_user_id_key", "users", ["telegram_user_id"])
