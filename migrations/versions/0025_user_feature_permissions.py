"""Add per-user binary feature grants without removing any working capability.

Revision ID: 0025_user_feature_permissions
Revises: 0024_bootstrap_flag_repair

Rollout policy (docs/FEATURE-PERMISSIONS.md): every existing user receives an explicit
row for every known key. Capabilities that already worked stay enabled; the new
`orders` capability starts disabled until an administrator enables it. Administrators
bypass grants in policy, so no toggle can lock them out.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_user_feature_permissions"
down_revision: str | None = "0024_bootstrap_flag_repair"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FEATURE_KEYS = (
    "ai_chat",
    "document_search",
    "document_ingest",
    "product_search",
    "image_search",
    "orders",
    "sales_reports",
    "external_search",
    "inventory",
    "reminders",
)


def upgrade() -> None:
    op.create_table(
        "user_feature_permissions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("feature_key", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_by_user_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("user_id", "feature_key", name="pk_user_feature_permissions"),
        sa.CheckConstraint(
            "feature_key IN ('ai_chat', 'document_search', 'document_ingest', "
            "'product_search', 'image_search', 'orders', 'sales_reports', "
            "'external_search', 'inventory', 'reminders')",
            name="ck_user_feature_permissions_key",
        ),
    )
    values = ", ".join(f"('{key}')" for key in _FEATURE_KEYS)
    op.execute(
        sa.text(
            f"""
            INSERT INTO user_feature_permissions (user_id, feature_key, enabled)
            SELECT u.id, k.feature_key, k.feature_key <> 'orders'
            FROM users u
            CROSS JOIN (VALUES {values}) AS k(feature_key)
            ON CONFLICT (user_id, feature_key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_table("user_feature_permissions")