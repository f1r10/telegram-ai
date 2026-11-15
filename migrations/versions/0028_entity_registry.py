"""Add the Phase-3 entity registry: entities, aliases and pending candidates.

Revision ID: 0028_entity_registry
Revises: 0027_order_reminder_delivery

A mention could only be resolved by re-reading the product/customer alias tables, so a
cross-document question had no way to name an identity or to leave an uncertain match for a
person to review. These three tables add a canonical registry that points at the existing
business rows (it copies none of them), the surface forms the registry needs beyond the two
record-level alias tables, and the pending candidates a weak match produces instead of a
silent merge. Additive only: no existing table or column is touched, and ``downgrade`` drops
exactly what ``upgrade`` created.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028_entity_registry"
down_revision: str | None = "0027_order_reminder_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("business_record_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_name", sa.String(length=512), nullable=False),
        sa.Column("normalized_name", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("entity_type IN ('product', 'customer')", name="ck_entities_type"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name="fk_entities_created_by", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_type", "business_record_id", name="uq_entities_business_record"
        ),
    )
    op.create_index("ix_entities_lookup", "entities", ["entity_type", "normalized_name"])

    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("alias", sa.String(length=512), nullable=False),
        sa.Column("normalized_alias", sa.String(length=512), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_reference_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["entities.id"], name="fk_entity_aliases_entity", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_entity_aliases_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_reference_id"],
            ["source_references.id"],
            name="fk_entity_aliases_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_id", "normalized_alias", name="uq_entity_alias_form"),
    )
    op.create_index("ix_entity_aliases_entity_id", "entity_aliases", ["entity_id"])
    op.create_index("ix_entity_aliases_lookup", "entity_aliases", ["normalized_alias"])

    op.create_table(
        "entity_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mention", sa.String(length=512), nullable=False),
        sa.Column("normalized_mention", sa.String(length=512), nullable=False),
        sa.Column("proposed_entity_id", sa.Uuid(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_user_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="ck_entity_candidates_status",
        ),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="ck_entity_candidates_score"),
        sa.ForeignKeyConstraint(
            ["proposed_entity_id"],
            ["entities.id"],
            name="fk_entity_candidates_entity",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_entity_candidates_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            name="fk_entity_candidates_reviewed_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "proposed_entity_id", "normalized_mention", name="uq_entity_candidate_mention"
        ),
    )
    op.create_index(
        "ix_entity_candidates_proposed_entity_id", "entity_candidates", ["proposed_entity_id"]
    )
    op.create_index(
        "ix_entity_candidates_review", "entity_candidates", ["normalized_mention", "status"]
    )


def downgrade() -> None:
    # Reverse order, and only what this revision created: no existing row is read or changed.
    op.drop_index("ix_entity_candidates_review", table_name="entity_candidates")
    op.drop_index("ix_entity_candidates_proposed_entity_id", table_name="entity_candidates")
    op.drop_table("entity_candidates")
    op.drop_index("ix_entity_aliases_lookup", table_name="entity_aliases")
    op.drop_index("ix_entity_aliases_entity_id", table_name="entity_aliases")
    op.drop_table("entity_aliases")
    op.drop_index("ix_entities_lookup", table_name="entities")
    op.drop_table("entities")
