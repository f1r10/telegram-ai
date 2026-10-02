"""Add raw file staging, ingestion sessions, and pending jobs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_ingestion_foundation"
down_revision: str | None = "0003_user_identity_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_objects",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('STAGED', 'ACTIVE', 'DELETED')", name="ck_file_objects_state"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
        sa.UniqueConstraint("sha256"),
    )
    op.create_index("ix_file_objects_sha256", "file_objects", ["sha256"], unique=True)

    op.create_table(
        "ingestion_sessions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('OPEN', 'COMMITTED', 'CANCELLED', 'EXPIRED')",
            name="ck_ingestion_sessions_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ingestion_sessions_user_id", "ingestion_sessions", ["user_id"])
    op.create_index(
        "uq_ingestion_sessions_open_user",
        "ingestion_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )

    op.create_table(
        "ingestion_session_items",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("file_object_id", sa.BigInteger(), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("sender_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_update_id", sa.BigInteger(), nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("item_type IN ('FILE', 'INSTRUCTION')", name="ck_ingestion_items_type"),
        sa.CheckConstraint(
            "(item_type = 'FILE' AND file_object_id IS NOT NULL) OR "
            "(item_type = 'INSTRUCTION' AND text_content IS NOT NULL)",
            name="ck_ingestion_items_payload",
        ),
        sa.ForeignKeyConstraint(["file_object_id"], ["file_objects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["session_id"], ["ingestion_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "sequence_no", name="uq_ingestion_item_sequence"),
        sa.UniqueConstraint(
            "telegram_chat_id", "telegram_message_id", name="uq_ingestion_telegram_message"
        ),
    )
    op.create_index(
        "ix_ingestion_session_items_session_id",
        "ingestion_session_items",
        ["session_id"],
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=2000), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="ck_ingestion_jobs_status",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["ingestion_sessions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )


def downgrade() -> None:
    op.drop_table("ingestion_jobs")
    op.drop_index("ix_ingestion_session_items_session_id", table_name="ingestion_session_items")
    op.drop_table("ingestion_session_items")
    op.drop_index("uq_ingestion_sessions_open_user", table_name="ingestion_sessions")
    op.drop_index("ix_ingestion_sessions_user_id", table_name="ingestion_sessions")
    op.drop_table("ingestion_sessions")
    op.drop_index("ix_file_objects_sha256", table_name="file_objects")
    op.drop_table("file_objects")
