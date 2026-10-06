"""Add worker lease state, parse runs, and notification outbox."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_processing_worker_state"
down_revision: str | None = "0006_submission_filename"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_ingestion_jobs_status", "ingestion_jobs", type_="check")
    op.create_check_constraint(
        "ck_ingestion_jobs_status",
        "ingestion_jobs",
        "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')",
    )
    op.add_column("ingestion_jobs", sa.Column("locked_at", sa.DateTime(timezone=True)))
    op.add_column("ingestion_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("ingestion_jobs", sa.Column("worker_id", sa.String(255)))
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_ingestion_jobs_available_at", "ingestion_jobs", ["available_at"])

    op.create_table(
        "document_parse_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("file_object_id", sa.BigInteger(), nullable=False),
        sa.Column("ingestion_job_id", sa.BigInteger(), nullable=False),
        sa.Column("session_item_id", sa.BigInteger()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("parser_name", sa.String(100), nullable=False),
        sa.Column("parser_version", sa.String(100), nullable=False),
        sa.Column("normalized_storage_key", sa.String(512)),
        sa.Column("text_storage_key", sa.String(512)),
        sa.Column("requires_ocr", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requires_vision", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(2000)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["file_object_id"], ["file_objects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ingestion_job_id"], ["ingestion_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_item_id"], ["ingestion_session_items.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'DEFERRED', 'FAILED')",
            name="ck_document_parse_runs_status",
        ),
    )
    op.create_index(
        "ix_document_parse_runs_job_file",
        "document_parse_runs",
        ["ingestion_job_id", "file_object_id"],
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("aggregate_type", sa.String(100), nullable=False),
        sa.Column("aggregate_id", sa.String(100), nullable=False),
        sa.Column("recipient_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(2000)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED')", name="ck_outbox_events_status"
        ),
    )
    op.create_index("ix_outbox_events_pending", "outbox_events", ["status", "available_at"])
    op.create_index("ix_outbox_events_recipient", "outbox_events", ["recipient_telegram_id"])


def downgrade() -> None:
    op.drop_index("ix_outbox_events_recipient", table_name="outbox_events")
    op.drop_index("ix_outbox_events_pending", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_document_parse_runs_job_file", table_name="document_parse_runs")
    op.drop_table("document_parse_runs")
    op.drop_index("ix_ingestion_jobs_available_at", table_name="ingestion_jobs")
    op.drop_column("ingestion_jobs", "available_at")
    op.drop_column("ingestion_jobs", "worker_id")
    op.drop_column("ingestion_jobs", "lease_expires_at")
    op.drop_column("ingestion_jobs", "locked_at")
    op.drop_constraint("ck_ingestion_jobs_status", "ingestion_jobs", type_="check")
