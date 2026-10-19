"""Persist operator-approved reservations without changing original stock movements.

Revision ID: 0021_stock_reservations
Revises: 0020_inventory_ledger
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_stock_reservations"
down_revision: str | None = "0020_inventory_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inventory_reservations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("location_key", sa.String(128), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("reference_key", sa.String(128), nullable=False),
        sa.Column("reference_label", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approved_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "product_id", "reference_key", name="uq_inventory_reservation_reference"
        ),
        sa.CheckConstraint("quantity > 0", name="ck_inventory_reservation_quantity_positive"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'CANCELLED', 'CONSUMED')",
            name="ck_inventory_reservation_status",
        ),
    )
    op.create_index(
        "ix_inventory_reservation_available",
        "inventory_reservations",
        ["product_id", "location_key", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_reservation_available", table_name="inventory_reservations")
    op.drop_table("inventory_reservations")
