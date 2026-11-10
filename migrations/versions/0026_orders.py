"""Create the real orders domain: durable drafts, review schedule and attachments.

Revision ID: 0026_orders
Revises: 0025_user_feature_permissions

Owner contract V2 §3.2-§3.4 (docs/V2-OWNER-REQUIREMENTS-2026-11-08.md; the private master
spec numbers the same rules §14-§15). The row is also the in-progress draft, so
sequential intake survives a restart; only confirmation promotes it to ACTIVE and writes
the review schedule, in the same transaction. Nothing here touches inventory balances.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_orders"
down_revision: str | None = "0025_user_feature_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ORDER_STATES = "'PENDING_CONFIRMATION', 'ACTIVE', 'FROZEN', 'COMPLETED', 'CANCELLED', 'DELETED'"
_REMINDER_STATES = (
    "'PENDING', 'CLAIMED', 'SENDING', 'SENT', 'RETRY_PENDING', 'FAILED', "
    "'SUPERSEDED', 'SKIPPED', 'CANCELLED'"
)


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("intake_token", sa.String(64), nullable=True),
        sa.Column("requested_by", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Baku"),
        sa.Column("deadline_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lead_time_minutes", sa.Integer(), nullable=True),
        sa.Column("warning_count", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="PENDING_CONFIRMATION"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(f"state IN ({_ORDER_STATES})", name="ck_orders_state"),
        sa.CheckConstraint(
            "lead_time_minutes IS NULL OR lead_time_minutes > 0",
            name="ck_orders_lead_time_positive",
        ),
        sa.CheckConstraint(
            "warning_count IS NULL OR (warning_count >= 1 AND warning_count <= 10)",
            name="ck_orders_warning_count_range",
        ),
        sa.CheckConstraint(
            "state NOT IN ('ACTIVE', 'FROZEN', 'COMPLETED') OR ("
            "requested_by IS NOT NULL AND deadline_utc IS NOT NULL "
            "AND lead_time_minutes IS NOT NULL AND warning_count IS NOT NULL "
            "AND description <> '')",
            name="ck_orders_confirmed_is_complete",
        ),
        sa.UniqueConstraint("intake_token", name="uq_orders_intake_token"),
    )
    op.create_index(
        "ix_orders_created_by_state_deadline",
        "orders",
        ["created_by_user_id", "state", "deadline_utc"],
    )
    # One open draft per account: intake cannot mix two orders in the same conversation.
    op.create_index(
        "uq_orders_one_open_draft",
        "orders",
        ["created_by_user_id"],
        unique=True,
        postgresql_where=sa.text("state = 'PENDING_CONFIRMATION'"),
    )

    op.create_table(
        "order_reminders",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.CheckConstraint(f"status IN ({_REMINDER_STATES})", name="ck_order_reminders_status"),
        sa.UniqueConstraint("order_id", "scheduled_for", name="uq_order_reminder_instant"),
        sa.UniqueConstraint("idempotency_key", name="uq_order_reminder_idempotency"),
    )
    op.create_index("ix_order_reminders_claim", "order_reminders", ["status", "scheduled_for"])
    op.create_index("ix_order_reminders_order", "order_reminders", ["order_id"])

    op.create_table(
        "order_attachments",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("file_object_id", sa.BigInteger(), nullable=False),
        sa.Column("added_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["added_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["file_object_id"], ["file_objects.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("order_id", "file_object_id", name="uq_order_attachment_file"),
        sa.UniqueConstraint("order_id", "position", name="uq_order_attachment_position"),
    )


def downgrade() -> None:
    op.drop_table("order_attachments")
    op.drop_index("ix_order_reminders_order", table_name="order_reminders")
    op.drop_index("ix_order_reminders_claim", table_name="order_reminders")
    op.drop_table("order_reminders")
    op.drop_index("uq_orders_one_open_draft", table_name="orders")
    op.drop_index("ix_orders_created_by_state_deadline", table_name="orders")
    op.drop_table("orders")
