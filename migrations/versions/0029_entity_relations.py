"""Add the Phase-3 relation layer: entity relations and their source evidence.

Revision ID: 0029_entity_relations
Revises: 0028_entity_registry

A resolved identity is not yet an answer: "what do we know about this customer?" needs the
relations between identities, and "no relation without a source" makes the source part of the row
instead of a convention. ``entity_relations`` stores one sourced, reviewable claim with a
verification status and a ``valid_from``/``valid_until`` window;
``entity_relation_evidence`` keeps the source facts behind it, append-only, so a rejected claim
still shows what a person saw.

Additive only: no existing table or column is touched, and ``downgrade`` drops exactly what
``upgrade`` created. Overlapping windows for one relation kind are left to the store and the unique
constraint on ``(entity_id, relation_type, related_entity_id, valid_from)``; a PostgreSQL exclusion
constraint would be stronger and is recorded as an open question.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_entity_relations"
down_revision: str | None = "0028_entity_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entity_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column("related_entity_id", sa.Uuid(), nullable=False),
        sa.Column(
            "verification_status",
            sa.String(length=16),
            nullable=False,
            server_default="inferred",
        ),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("source_reference_id", sa.Uuid(), nullable=False),
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
            "entity_type IN ('product', 'customer')", name="ck_entity_relations_entity_type"
        ),
        sa.CheckConstraint(
            "relation_type IN ('supplied_to')", name="ck_entity_relations_relation_type"
        ),
        sa.CheckConstraint(
            "verification_status IN ('confirmed', 'inferred', 'rejected')",
            name="ck_entity_relations_verification",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="ck_entity_relations_validity",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["entities.id"], name="fk_entity_relations_entity", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["related_entity_id"],
            ["entities.id"],
            name="fk_entity_relations_related_entity",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_reference_id"],
            ["source_references.id"],
            name="fk_entity_relations_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_entity_relations_created_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            name="fk_entity_relations_reviewed_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_id",
            "relation_type",
            "related_entity_id",
            "valid_from",
            name="uq_entity_relations_window",
        ),
    )
    op.create_index(
        "ix_entity_relations_hop",
        "entity_relations",
        ["entity_id", "relation_type", "verification_status"],
    )
    op.create_index("ix_entity_relations_related", "entity_relations", ["related_entity_id"])

    op.create_table(
        "entity_relation_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("relation_id", sa.Uuid(), nullable=False),
        sa.Column("source_reference_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("length(reason) > 0", name="ck_entity_relation_evidence_reason"),
        sa.ForeignKeyConstraint(
            ["relation_id"],
            ["entity_relations.id"],
            name="fk_entity_relation_evidence_relation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_reference_id"],
            ["source_references.id"],
            name="fk_entity_relation_evidence_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_entity_relation_evidence_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "relation_id", "source_reference_id", name="uq_entity_relation_evidence_source"
        ),
    )
    op.create_index(
        "ix_entity_relation_evidence_relation_id", "entity_relation_evidence", ["relation_id"]
    )


def downgrade() -> None:
    # Reverse order, and only what this revision created: no existing row is read or changed.
    op.drop_index("ix_entity_relation_evidence_relation_id", table_name="entity_relation_evidence")
    op.drop_table("entity_relation_evidence")
    op.drop_index("ix_entity_relations_related", table_name="entity_relations")
    op.drop_index("ix_entity_relations_hop", table_name="entity_relations")
    op.drop_table("entity_relations")
