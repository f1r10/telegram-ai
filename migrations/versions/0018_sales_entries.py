"""Create explicitly confirmed completed-sales ledger (never quotations or payments).

Revision ID: 0018_sales_entries
Revises: 0017_pending_image_confirmations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_sales_entries"
down_revision: str | None = "0017_pending_image_confirmations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sales_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("customer_key", sa.String(255), nullable=False),
        sa.Column("invoice_number", sa.String(100), nullable=False),
        sa.Column("sold_on", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="POSTED"
        ),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("amount > 0", name="ck_sales_amount_positive"),
        sa.CheckConstraint(
            "status IN ('POSTED', 'VOIDED')", name="ck_sales_status"
        ),
        sa.UniqueConstraint(
            "customer_key", "invoice_number", name="uq_sales_customer_invoice"
        ),
    )
    op.create_index(
        "ix_sales_customer_date", "sales_entries", ["customer_key", "sold_on"]
    )


def downgrade() -> None:
    op.drop_index("ix_sales_customer_date", table_name="sales_entries")
    op.drop_table("sales_entries")
