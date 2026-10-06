"""Add reminder tables and user timezone.

Revision ID: 0014_add_reminders
Revises: 0013_web_search_evidence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_add_reminders"
down_revision: str | None = "0013_web_search_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "timezone",
            sa.String(64),
            nullable=False,
            server_default="Asia/Baku",
        ),
    )
    op.create_table(
        "reminders",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("due_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recurrence_rule", sa.String(255), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'PAUSED', 'SENT', 'CANCELLED')", name="ck_reminders_status"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_reminders_idempotency"),
    )
    op.create_index("ix_reminders_user_id", "reminders", ["user_id"])
    op.create_index("ix_reminders_due_claim", "reminders", ["status", "due_at_utc"])
    op.create_table(
        "reminder_deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("reminder_id", sa.BigInteger(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["reminder_id"], ["reminders.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('CLAIMED', 'SENDING', 'SENT', 'RETRY_PENDING', 'FAILED')",
            name="ck_reminder_deliveries_status",
        ),
        sa.UniqueConstraint("reminder_id", "scheduled_for", name="uq_reminder_delivery"),
    )
    op.create_index("ix_reminder_deliveries_reminder", "reminder_deliveries", ["reminder_id"])


def downgrade() -> None:
    op.drop_index("ix_reminder_deliveries_reminder", table_name="reminder_deliveries")
    op.drop_table("reminder_deliveries")
    op.drop_index("ix_reminders_due_claim", table_name="reminders")
    op.drop_index("ix_reminders_user_id", table_name="reminders")
    op.drop_table("reminders")
    op.drop_column("users", "timezone")
