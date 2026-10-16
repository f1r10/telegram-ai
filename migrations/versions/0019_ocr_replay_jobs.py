"""Durable operator-controlled OCR replay queue for old scanned PDFs.

Revision ID: 0019_ocr_replay_jobs
Revises: 0018_sales_entries
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_ocr_replay_jobs"
down_revision: str | None = "0018_sales_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ocr_replay_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("file_object_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("source_parse_run_id", sa.BigInteger(), nullable=False),
        sa.Column("result_parse_run_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["file_object_id"], ["file_objects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_parse_run_id"], ["document_parse_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["result_parse_run_id"], ["document_parse_runs.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'INDEX_QUEUED', 'FAILED', 'SKIPPED')",
            name="ck_ocr_replay_job_status",
        ),
    )
    op.create_index("ix_ocr_replay_due", "ocr_replay_jobs", ["status", "available_at"])


def downgrade() -> None:
    op.drop_index("ix_ocr_replay_due", table_name="ocr_replay_jobs")
    op.drop_table("ocr_replay_jobs")
