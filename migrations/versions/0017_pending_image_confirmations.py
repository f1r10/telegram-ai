"""Add the queued product-picture confirmation that /saxla finalizes.

Revision ID: 0017_pending_image_confirmations
Revises: 0016_product_image_relations

Master Spec §23.7 makes ``Bu KA45-dir, yadda saxla`` deterministic and says a cancel must not
produce an authoritative relation. Because ``/saxla`` is the ingestion commit boundary, the caption
records an intent that lives with the session rather than writing straight into
``product_image_relations``. The partial unique index keeps that intent single per message item, so
one caption cannot queue the same claim twice.

The revision id is shorter than the table it adds because ``alembic_version.version_num`` is a
``varchar(32)`` column in this schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_pending_image_confirmations"
down_revision: str | None = "0016_product_image_relations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_product_image_confirmations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("session_item_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_code", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["ingestion_sessions.id"],
            ondelete="CASCADE",
            name="fk_pending_product_image_confirmations_session",
        ),
        sa.ForeignKeyConstraint(
            ["session_item_id"],
            ["ingestion_session_items.id"],
            ondelete="CASCADE",
            name="fk_pending_product_image_confirmations_item",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            ondelete="RESTRICT",
            name="fk_pending_product_image_confirmations_product",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'LINKED', 'CANCELLED')",
            name="ck_pending_product_image_confirmations_status",
        ),
    )
    op.create_index(
        "ix_pending_product_image_confirmations_session",
        "pending_product_image_confirmations",
        ["session_id"],
    )
    op.create_index(
        "ix_pending_product_image_confirmations_product",
        "pending_product_image_confirmations",
        ["product_id"],
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_pending_product_image_open_item
            ON pending_product_image_confirmations (session_item_id)
            WHERE status = 'PENDING'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_pending_product_image_open_item")
    op.drop_index(
        "ix_pending_product_image_confirmations_product",
        table_name="pending_product_image_confirmations",
    )
    op.drop_index(
        "ix_pending_product_image_confirmations_session",
        table_name="pending_product_image_confirmations",
    )
    op.drop_table("pending_product_image_confirmations")
