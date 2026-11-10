"""Let the outbox record a delivery that was stopped rather than one that failed.

Revision ID: 0027_order_reminder_delivery
Revises: 0026_orders

A review reminder is claimed and queued before it is sent, because the claim has to be
atomic (§15.3). If the order is frozen, finished, cancelled or deleted in the seconds
between the claim and the send, the consumer must stop the message without calling that a
failed delivery: "Do not mark failed delivery as sent" cuts both ways, and a suppression
counted as a failure would put noise into the operational view (§26). ``CANCELLED`` says
what happened, and the matching ``order_reminders`` row keeps the same word for it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027_order_reminder_delivery"
down_revision: str | None = "0026_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "status IN ('PENDING', 'SENT', 'FAILED')"
_NEW = "status IN ('PENDING', 'SENDING', 'SENT', 'CANCELLED', 'FAILED')"
_CONSTRAINT = "ck_outbox_events_status"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "outbox_events", type_="check")
    op.create_check_constraint(_CONSTRAINT, "outbox_events", _NEW)


def downgrade() -> None:
    # A stopped delivery has no word in the old vocabulary, so it falls back to the only
    # terminal one that is not "sent" before the narrower constraint goes back on.
    op.execute(
        "update outbox_events set status = 'FAILED' where status IN ('CANCELLED', 'SENDING')"
    )
    op.drop_constraint(_CONSTRAINT, "outbox_events", type_="check")
    op.create_check_constraint(_CONSTRAINT, "outbox_events", _OLD)
