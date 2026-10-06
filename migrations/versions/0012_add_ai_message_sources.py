"""Add bounded AI response citation mappings.

Revision ID: 0012_ai_message_sources
Revises: 0011_structured_extraction
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_ai_message_sources"
down_revision: str | None = "0011_structured_extraction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_message_sources",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("citation_index", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=True),
        sa.Column("filename", sa.String(length=512), nullable=True),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["file_objects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["message_id"], ["ai_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", "citation_index", name="uq_ai_message_source_citation"),
    )
    op.create_index(
        "ix_ai_message_sources_expiry", "ai_message_sources", ["expires_at"], unique=False
    )
    op.create_index(
        "ix_ai_message_sources_message_id", "ai_message_sources", ["message_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ai_message_sources_message_id", table_name="ai_message_sources")
    op.drop_index("ix_ai_message_sources_expiry", table_name="ai_message_sources")
    op.drop_table("ai_message_sources")
