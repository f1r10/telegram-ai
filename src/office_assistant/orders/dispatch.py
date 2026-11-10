"""Worker-side delivery of the review reminders an order was confirmed with.

Owner contract §15.3 (master spec) and §3.3: the plan already exists as one durable
``order_reminders`` row per scheduled instant, written by the same transaction that
confirmed the order. This module is the only thing that turns a due row into a send.

Claiming is atomic through ``FOR UPDATE SKIP LOCKED``: exactly one worker moves a due row
to ``CLAIMED`` and enqueues its outbox event in the same transaction, so two workers, a
retry and a restart all produce one message per instant. Only ``PENDING`` rows are
claimed — a failed send is retried by the outbox consumer against the *same* event, and
re-claiming a ``RETRY_PENDING`` row here would queue a second message for one instant.

Delivery goes to the account that created the order and to nobody else (§15.2), and the
order has to be ``ACTIVE`` both when the row is claimed and when the message is actually
sent, which is what keeps a freeze, a completion or a cancellation that lands in between
from producing a reminder the user asked to stop.
"""

import logging
from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.core.clock import Clock
from office_assistant.infrastructure.database.models import (
    Order,
    OrderReminder,
    OrderReminderStatus,
    OrderState,
    OutboxEvent,
    OutboxEventStatus,
    User,
    UserStatus,
)
from office_assistant.repositories.audit import AuditRepository

logger = logging.getLogger(__name__)

ORDER_REMINDER_EVENT_TYPE = "ORDER_REMINDER_DUE"

# What a row that never reached a send is called afterwards: the instant was planned, then
# stopped. ``SKIPPED`` would claim it was stale when planned, which is a different fact.
_STOPPED = OrderReminderStatus.CANCELLED.value


def record_attempt(
    session: AsyncSession,
    entry: OrderReminder,
    event: OutboxEvent,
    outcome: str,
    *,
    message_id: int | None = None,
) -> None:
    """Append a redacted attempt event to the existing durable, owner-visible history."""

    AuditRepository(session).add(
        actor_user_id=None,
        action=f"ORDER_REMINDER_{outcome}",
        entity_type="order",
        entity_id=str(entry.order_id),
        metadata={
            "reminder_id": int(entry.id),
            "event_id": int(event.id),
            "attempt": event.attempt_count,
            "outcome": outcome,
            "error_code": event.last_error,
            "telegram_message_id": message_id,
        },
    )


class OrderReminderDispatchService:
    """Claims due review reminders and hands them to the durable outbox."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Clock,
        batch_size: int = 20,
        lease_seconds: int = 300,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._batch_size = batch_size
        self._lease = timedelta(seconds=lease_seconds)

    async def claim_due(self) -> int:
        """Turn every due, still-live plan entry into exactly one queued delivery."""

        now = self._clock.now_utc()
        claimed = 0
        async with self._session_factory.begin() as session:
            # Order before ledger, as in lifecycle mutations and the sender. Workers
            # skip a busy order instead of holding its ledger while waiting for it.
            orders = (
                await session.scalars(
                    select(Order)
                    .where(
                        Order.id.in_(
                            select(OrderReminder.order_id).where(
                                OrderReminder.status == OrderReminderStatus.PENDING.value,
                                OrderReminder.scheduled_for <= now,
                            )
                        )
                    )
                    .order_by(Order.id)
                    .limit(self._batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            processed = 0
            for order in orders:
                due = (
                    await session.scalars(
                        select(OrderReminder)
                        .where(
                            OrderReminder.order_id == order.id,
                            OrderReminder.status == OrderReminderStatus.PENDING.value,
                            OrderReminder.scheduled_for <= now,
                        )
                        .order_by(OrderReminder.scheduled_for)
                        .limit(self._batch_size - processed)
                        .with_for_update()
                    )
                ).all()
                creator = await session.get(User, order.created_by_user_id)
                for entry in due:
                    processed += 1
                    if (
                        order.state != OrderState.ACTIVE.value
                        or creator is None
                        or creator.status != UserStatus.ACTIVE.value
                    ):
                        entry.status = _STOPPED
                        continue
                    entry.status = OrderReminderStatus.CLAIMED.value
                    entry.claimed_at = now
                    session.add(
                        OutboxEvent(
                            event_type=ORDER_REMINDER_EVENT_TYPE,
                            aggregate_type="order",
                            aggregate_id=str(order.id),
                            recipient_telegram_id=creator.telegram_user_id,
                            payload={
                                "order_id": int(order.id),
                                "reminder_id": int(entry.id),
                                "index": entry.ordinal + 1,
                                "total": order.warning_count or 0,
                            },
                            status=OutboxEventStatus.PENDING.value,
                            available_at=now,
                        )
                    )
                    claimed += 1
                if processed >= self._batch_size:
                    break
        if claimed:
            logger.info("Order reminder claim completed | count=%s", claimed)
        return claimed

    async def reclaim_stale(self) -> int:
        """Close the ledger honestly after a crash left a row claimed or sending.

        The row and its event are committed together, so the event says what really
        happened. A row whose event cannot be found is failed rather than requeued: the
        instant is in the past by definition here, and replaying it would be the burst
        §15.4 forbids.
        """

        cutoff = self._clock.now_utc() - self._lease
        live = (
            OrderReminderStatus.CLAIMED.value,
            OrderReminderStatus.SENDING.value,
            OrderReminderStatus.RETRY_PENDING.value,
        )
        recovered = 0
        async with self._session_factory.begin() as session:
            # Read candidate IDs without locks, then use the sender's event -> order ->
            # ledger lock order. Still-pending events must not starve crashed sends.
            candidates = (
                await session.execute(
                    select(OrderReminder.id, OrderReminder.order_id, OutboxEvent.id)
                    .outerjoin(
                        OutboxEvent,
                        and_(
                            OutboxEvent.event_type == ORDER_REMINDER_EVENT_TYPE,
                            OutboxEvent.payload["reminder_id"].as_integer() == OrderReminder.id,
                        ),
                    )
                    .where(
                        OrderReminder.claimed_at <= cutoff,
                        or_(
                            OutboxEvent.status == OutboxEventStatus.SENDING.value,
                            and_(
                                OrderReminder.status.in_(live),
                                or_(
                                    OutboxEvent.id.is_(None),
                                    OutboxEvent.status != OutboxEventStatus.PENDING.value,
                                ),
                            ),
                        ),
                    )
                    .order_by(OrderReminder.claimed_at, OrderReminder.id)
                    .limit(self._batch_size)
                )
            ).all()
            for entry_id, order_id, event_id in candidates:
                event = None
                if event_id is not None:
                    event = await session.scalar(
                        select(OutboxEvent)
                        .where(OutboxEvent.id == event_id)
                        .with_for_update(skip_locked=True)
                    )
                    if event is None:
                        continue  # a live sender owns the event, even if its lease is old
                order = await session.scalar(
                    select(Order).where(Order.id == order_id).with_for_update(skip_locked=True)
                )
                if order is None:
                    continue
                entry = await session.scalar(
                    select(OrderReminder).where(OrderReminder.id == entry_id).with_for_update()
                )
                if entry is None or entry.claimed_at is None or entry.claimed_at > cutoff:
                    continue
                if event is not None and event.status == OutboxEventStatus.PENDING.value:
                    continue
                if event is not None and event.status == OutboxEventStatus.SENDING.value:
                    if entry.status in (
                        OrderReminderStatus.CANCELLED.value,
                        OrderReminderStatus.SUPERSEDED.value,
                    ):
                        event.status = OutboxEventStatus.CANCELLED.value
                        record_attempt(session, entry, event, "CANCELLED")
                    else:
                        # Telegram cannot reconcile an interrupted send by client key.
                        # Never turn uncertainty into a duplicate or claim it was sent.
                        event.status = OutboxEventStatus.FAILED.value
                        entry.status = OrderReminderStatus.FAILED.value
                        entry.last_error = event.last_error = "DELIVERY_UNKNOWN"
                        record_attempt(session, entry, event, "UNKNOWN")
                elif entry.status not in live:
                    continue
                elif event is None:
                    entry.status = OrderReminderStatus.FAILED.value
                    entry.last_error = "claimed without a durable outbox event"
                elif event.status == OutboxEventStatus.SENT.value:
                    entry.status = OrderReminderStatus.SENT.value
                    entry.sent_at = event.sent_at
                elif event.status == OutboxEventStatus.CANCELLED.value:
                    entry.status = _STOPPED
                else:
                    entry.status = OrderReminderStatus.FAILED.value
                    entry.last_error = event.last_error
                recovered += 1
        if recovered:
            logger.info("Order reminder stale-lease recovery completed | count=%s", recovered)
        return recovered
