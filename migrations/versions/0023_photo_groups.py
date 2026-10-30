"""Add owner-scoped persistent photo groups without modifying original files.

Revision ID: 0023_photo_groups
Revises: 0022_inventory_units
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_photo_groups"
down_revision: str | None = "0022_inventory_units"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "photo_groups",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_session_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("metadata_text", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_session_id"], ["ingestion_sessions.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index("ix_photo_groups_owner_user_id", "photo_groups", ["owner_user_id"])
    op.create_table(
        "photo_group_images",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("group_id", sa.BigInteger(), nullable=False),
        sa.Column("session_item_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["group_id"], ["photo_groups.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["session_item_id"], ["ingestion_session_items.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("group_id", "position", name="uq_photo_group_image_position"),
        sa.UniqueConstraint(
            "group_id", "session_item_id", name="uq_photo_group_image_source"
        ),
    )
    op.create_index("ix_photo_group_images_group_id", "photo_group_images", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_photo_group_images_group_id", table_name="photo_group_images")
    op.drop_table("photo_group_images")
    op.drop_index("ix_photo_groups_owner_user_id", table_name="photo_groups")
    op.drop_table("photo_groups")
