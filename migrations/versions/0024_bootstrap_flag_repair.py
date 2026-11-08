"""Repair a missing bootstrap flag on databases provisioned without 0002's data row.

Legacy/staging databases can carry the full schema (and even an ACTIVE admin)
while ``system_metadata`` lacks ``bootstrap.admin.initialized`` — e.g. schema-only
provisioning or a partial restore. AuthenticationService then rejects every
password attempt with AuthenticationConfigurationError, so a second Telegram
user can never request access. This additive, idempotent repair restores the
flag and derives its value from existing admins so a second bootstrap admin can
never be created.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_bootstrap_flag_repair"
down_revision: str | None = "0023_photo_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO system_metadata (key, value)
            SELECT 'bootstrap.admin.initialized',
                   CASE WHEN EXISTS (SELECT 1 FROM users WHERE role = 'ADMIN')
                        THEN 'true' ELSE 'false' END
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Additive repair: removing bootstrap state would break password login again.
    pass