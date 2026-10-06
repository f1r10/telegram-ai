"""Add external web search cache and URL citation mappings.

Revision ID: 0013_web_search_evidence
Revises: 0012_ai_message_sources
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_web_search_evidence"
down_revision: str | None = "0012_ai_message_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_message_sources", sa.Column("external_url", sa.String(4000)))
    op.add_column("ai_message_sources", sa.Column("external_title", sa.String(1000)))
    op.create_table(
        "web_search_cache",
        sa.Column("query_key", sa.String(64), primary_key=True),
        sa.Column("normalized_query", sa.String(1000), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_web_search_cache_expires", "web_search_cache", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_web_search_cache_expires", table_name="web_search_cache")
    op.drop_table("web_search_cache")
    op.drop_column("ai_message_sources", "external_title")
    op.drop_column("ai_message_sources", "external_url")
