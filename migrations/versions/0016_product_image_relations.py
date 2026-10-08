"""Add the product ↔ image relation that a user's own words create.

Revision ID: 0016_product_image_relations
Revises: 0015_image_asset_foundation

Master Spec §23.6 asks for a first-class relation carrying role, reference flag, confirmation type,
provenance, actor and history timestamps. §22.7 then forbids the silent part: once a picture is
confirmed for one product, naming a second one is a conflict, not an update. Both partial unique
indexes are written here so the database states that rule rather than the application promising it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_product_image_relations"
down_revision: str | None = "0015_image_asset_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_image_relations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("image_asset_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("is_reference", sa.Boolean(), nullable=False),
        sa.Column("confirmation_type", sa.String(32), nullable=False),
        sa.Column("source_reference_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            ondelete="RESTRICT",
            name="fk_product_image_relations_product",
        ),
        sa.ForeignKeyConstraint(
            ["image_asset_id"],
            ["image_assets.id"],
            ondelete="RESTRICT",
            name="fk_product_image_relations_asset",
        ),
        sa.ForeignKeyConstraint(
            ["source_reference_id"],
            ["source_references.id"],
            ondelete="RESTRICT",
            name="fk_product_image_relations_source",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_product_image_relations_actor",
        ),
        sa.CheckConstraint(
            "role IN ('USER_CONFIRMED', 'REFERENCE', 'CATALOG', 'PACKAGING', "
            "'DOCUMENT_EXTRACTED', 'OTHER')",
            name="ck_product_image_relations_role",
        ),
        sa.CheckConstraint(
            "confirmation_type IN ('USER_EXPLICIT')",
            name="ck_product_image_relations_confirmation",
        ),
    )
    op.create_index("ix_product_image_relations_product", "product_image_relations", ["product_id"])
    op.create_index(
        "ix_product_image_relations_asset", "product_image_relations", ["image_asset_id"]
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_product_image_active_confirmation
            ON product_image_relations (image_asset_id)
            WHERE confirmation_type = 'USER_EXPLICIT' AND superseded_at IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_product_image_relation_active
            ON product_image_relations (product_id, image_asset_id, role)
            WHERE superseded_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_product_image_relation_active")
    op.execute("DROP INDEX IF EXISTS uq_product_image_active_confirmation")
    op.drop_index("ix_product_image_relations_asset", table_name="product_image_relations")
    op.drop_index("ix_product_image_relations_product", table_name="product_image_relations")
    op.drop_table("product_image_relations")
