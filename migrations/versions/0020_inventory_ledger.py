"""Create warehouse inventory opening balances and immutable stock movements.

Revision ID: 0020_inventory_ledger
Revises: 0019_ocr_replay_jobs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_inventory_ledger"
down_revision: str | None = "0019_ocr_replay_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inventory_balances",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("location_key", sa.String(128), nullable=False),
        sa.Column("location_name", sa.String(128), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("product_id", "location_key", name="uq_inventory_product_location"),
        sa.CheckConstraint("quantity >= 0", name="ck_inventory_balance_nonnegative"),
    )
    op.create_index("ix_inventory_balances_product", "inventory_balances", ["product_id"])
    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("location_key", sa.String(128), nullable=False),
        sa.Column("location_name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("balance_after", sa.Numeric(18, 3), nullable=False),
        sa.Column("reference_key", sa.String(128), nullable=False),
        sa.Column("reference_label", sa.String(128), nullable=False),
        sa.Column("approved_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("product_id", "location_key", "reference_key", name="uq_inventory_reference"),
        sa.CheckConstraint("kind IN ('OPENING', 'RECEIPT', 'ISSUE')", name="ck_inventory_movement_kind"),
        sa.CheckConstraint("quantity > 0 OR kind = 'OPENING'", name="ck_inventory_movement_positive"),
        sa.CheckConstraint("quantity >= 0", name="ck_inventory_movement_nonnegative"),
        sa.CheckConstraint("balance_after >= 0", name="ck_inventory_movement_balance_nonnegative"),
    )
    op.create_index(
        "ix_inventory_movement_product_created",
        "inventory_movements", ["product_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_movement_product_created", table_name="inventory_movements")
    op.drop_table("inventory_movements")
    op.drop_index("ix_inventory_balances_product", table_name="inventory_balances")
    op.drop_table("inventory_balances")
