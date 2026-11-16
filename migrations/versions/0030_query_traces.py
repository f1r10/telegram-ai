"""Add the Phase-3 query-trace table: what the engine read, for whom, and at what cost.

Revision ID: 0030_query_traces
Revises: 0029_entity_relations

A sourced answer is only checkable if the read behind it can be inspected too. ``query_traces``
records one row per statement the entity layer actually executed: the stage, the plan that ran
(statement text and bound values), the source usage, the row count and — only when it was measured —
the latency. ``latency_ms`` is nullable on purpose so an unmeasured read records no number instead
of a zero that would look like a fast query.

Additive only: no existing table or column is touched, and ``downgrade`` drops exactly what
``upgrade`` created.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_query_traces"
down_revision: str | None = "0029_entity_relations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "query_traces",
        sa.Column("id", sa.Uuid(), nullable=False),
        # The caller's grouping id: every statement of one answer shares it. Not unique, because a
        # trace is a sequence of statements rather than a single row.
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("plan_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "source_usage_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "stage IN ('resolution', 'relation_one_hop', 'relation_multi_hop')",
            name="ck_query_traces_stage",
        ),
        sa.CheckConstraint("row_count >= 0", name="ck_query_traces_row_count"),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_query_traces_latency"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_query_traces_user", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_query_traces_trace", "query_traces", ["trace_id", "created_at"])
    op.create_index("ix_query_traces_user", "query_traces", ["user_id", "created_at"])


def downgrade() -> None:
    # Only what this revision created: no existing row is read or changed.
    op.drop_index("ix_query_traces_user", table_name="query_traces")
    op.drop_index("ix_query_traces_trace", table_name="query_traces")
    op.drop_table("query_traces")
