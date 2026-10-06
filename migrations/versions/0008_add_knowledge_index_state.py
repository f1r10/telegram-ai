"""Add durable knowledge-index jobs and per-parse index state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_knowledge_index_state"
down_revision: str | None = "0007_processing_worker_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_index_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ingestion_job_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collection_alias", sa.String(255), nullable=False),
        sa.Column("collection_name", sa.String(255), nullable=False),
        sa.Column("embedding_model", sa.String(255), nullable=False),
        sa.Column("chunking_version", sa.String(100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("worker_id", sa.String(255)),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_error", sa.String(2000)),
        sa.ForeignKeyConstraint(["ingestion_job_id"], ["ingestion_jobs.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')",
            name="ck_knowledge_index_jobs_status",
        ),
    )
    op.create_index(
        "ix_knowledge_index_jobs_available_at", "knowledge_index_jobs", ["available_at"]
    )

    op.create_table(
        "knowledge_index_records",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("knowledge_index_job_id", sa.BigInteger(), nullable=False),
        sa.Column("parse_run_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("collection_name", sa.String(255), nullable=False),
        sa.Column("embedding_model", sa.String(255), nullable=False),
        sa.Column("chunking_version", sa.String(100), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indexed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(2000)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_index_job_id"], ["knowledge_index_jobs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["parse_run_id"], ["document_parse_runs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "knowledge_index_job_id", "parse_run_id", name="uq_knowledge_index_job_parse"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_knowledge_index_records_status",
        ),
    )
    op.create_index(
        "ix_knowledge_index_records_parse_run", "knowledge_index_records", ["parse_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_index_records_parse_run", table_name="knowledge_index_records")
    op.drop_table("knowledge_index_records")
    op.drop_index("ix_knowledge_index_jobs_available_at", table_name="knowledge_index_jobs")
    op.drop_table("knowledge_index_jobs")
