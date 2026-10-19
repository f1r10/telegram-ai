"""Add opt-in authorized warehouse registry and canonical product stock units.

Revision ID: 0022_inventory_units
Revises: 0021_stock_reservations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_inventory_units"
down_revision: str | None = "0021_stock_reservations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inventory_warehouses",
        sa.Column("location_key", sa.String(128), primary_key=True),
        sa.Column("location_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approved_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED')", name="ck_inventory_warehouse_status"
        ),
    )
    op.create_table(
        "inventory_product_units",
        sa.Column("product_id", sa.Uuid(), primary_key=True),
        sa.Column("base_unit", sa.String(8), nullable=False),
        sa.Column("approved_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "base_unit IN ('PCS', 'KG', 'L', 'BOX')", name="ck_inventory_product_base_unit"
        ),
    )
    op.create_table(
        "inventory_unit_conversions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("from_unit", sa.String(8), nullable=False),
        sa.Column("factor", sa.Numeric(18, 3), nullable=False),
        sa.Column("approved_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "product_id", "from_unit", name="uq_inventory_conversion_product_from"
        ),
        sa.CheckConstraint(
            "from_unit IN ('PCS', 'KG', 'L', 'BOX')", name="ck_inventory_conversion_from_unit"
        ),
        sa.CheckConstraint("factor > 0", name="ck_inventory_conversion_factor_positive"),
    )


def downgrade() -> None:
    op.drop_table("inventory_unit_conversions")
    op.drop_table("inventory_product_units")
    op.drop_table("inventory_warehouses")
