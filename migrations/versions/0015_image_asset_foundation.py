"""Add image asset content and provenance tables.

Revision ID: 0015_image_asset_foundation
Revises: 0014_add_reminders

Master Spec §23.4 separates content from occurrence: bytes are stored once per sha256 while every
sighting keeps its own locator row. That is what lets §23.3 hold both ways at once — a derived image
is never a fake user upload, yet it still carries full provenance.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_image_asset_foundation"
down_revision: str | None = "0014_add_reminders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "image_assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("storage_kind", sa.String(32), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("storage_kind IN ('LOCAL')", name="ck_image_assets_storage_kind"),
        sa.CheckConstraint("byte_size > 0", name="ck_image_assets_byte_size"),
        sa.UniqueConstraint("content_sha256", name="uq_image_assets_content_sha256"),
        sa.UniqueConstraint("storage_key", name="uq_image_assets_storage_key"),
    )
    op.create_index("ix_image_assets_content_sha256", "image_assets", ["content_sha256"])
    op.create_table(
        "image_asset_occurrences",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("image_asset_id", sa.Uuid(), nullable=False),
        sa.Column("origin_type", sa.String(32), nullable=False),
        sa.Column("source_file_id", sa.BigInteger(), nullable=False),
        sa.Column("source_parse_run_id", sa.BigInteger(), nullable=True),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("locator_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["image_asset_id"], ["image_assets.id"], ondelete="CASCADE",
            name="fk_image_asset_occurrences_asset",
        ),
        sa.ForeignKeyConstraint(
            ["source_file_id"], ["file_objects.id"], ondelete="CASCADE",
            name="fk_image_asset_occurrences_source_file",
        ),
        sa.ForeignKeyConstraint(
            ["source_parse_run_id"], ["document_parse_runs.id"], ondelete="CASCADE",
            name="fk_image_asset_occurrences_parse_run",
        ),
        sa.CheckConstraint(
            "origin_type IN ('STANDALONE_UPLOAD', 'PDF_EMBEDDED', 'DOCX_EMBEDDED', "
            "'XLSX_EMBEDDED')",
            name="ck_image_asset_occurrences_origin",
        ),
        sa.UniqueConstraint(
            "image_asset_id",
            "origin_type",
            "source_file_id",
            "source_parse_run_id",
            "locator_sha256",
            name="uq_image_asset_occurrence",
        ),
    )
    op.create_index(
        "ix_image_asset_occurrences_asset", "image_asset_occurrences", ["image_asset_id"]
    )
    op.create_index(
        "ix_image_asset_occurrences_file", "image_asset_occurrences", ["source_file_id"]
    )
    op.create_index(
        "ix_image_asset_occurrences_run", "image_asset_occurrences", ["source_parse_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_image_asset_occurrences_run", table_name="image_asset_occurrences")
    op.drop_index("ix_image_asset_occurrences_file", table_name="image_asset_occurrences")
    op.drop_index("ix_image_asset_occurrences_asset", table_name="image_asset_occurrences")
    op.drop_table("image_asset_occurrences")
    op.drop_index("ix_image_assets_content_sha256", table_name="image_assets")
    op.drop_table("image_assets")
