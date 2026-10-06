"""Worker-side reminder dispatcher (SPEC §5).

Claiming is atomic through ``FOR UPDATE SKIP LOCKED``: exactly one worker turns
a due reminder into a ``CLAIMED`` delivery plus a durable outbox event in one
transaction. The Telegram token stays in the app process; the app's outbox
consumer performs the send and drives the delivery state machine
(``claimed -> sending -> sent`` / ``retry_pending -> failed``).

Policy: at-least-once. A crash between send and commit can rarely duplicate a
delivery; this is accepted and documented in SPEC §5.
"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.core.clock import Clock
from office_assistant.infrastructure.database.models import (
    OutboxEvent,
    OutboxEventStatus,
    Reminder,
    ReminderDelivery,
    ReminderDeliveryStatus,
    ReminderStatus,
    User,
)

logger = logging.getLogger(__name__)

REMINDER_EVENT_TYPE = "REMINDER_DUE"


class ReminderDispatchService:
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
        """Atomically claim due reminders and enqueue delivery events."""
        now = self._clock.now_utc()
        claimed = 0
        async with self._session_factory.begin() as session:
            due = (
                (
                    await session.execute(
                        select(Reminder)
                        .where(
                            Reminder.status == ReminderStatus.ACTIVE.value,
                            Reminder.due_at_utc <= now,
                        )
                        .order_by(Reminder.due_at_utc)
                        .limit(self._batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for reminder in due:
                already = (
                    await session.execute(
                        select(ReminderDelivery.id).where(
                            ReminderDelivery.reminder_id == reminder.id,
                            ReminderDelivery.scheduled_for == reminder.due_at_utc,
                        )
                    )
                ).scalar_one_or_none()
                if already is not None:
                    reminder.status = ReminderStatus.PAUSED.value
                    continue
                owner = await session.get(User, reminder.user_id)
                if owner is None:
                    reminder.status = ReminderStatus.CANCELLED.value
                    continue
                delivery = ReminderDelivery(
                    reminder_id=reminder.id,
                    scheduled_for=reminder.due_at_utc,
                    status=ReminderDeliveryStatus.CLAIMED.value,
                    attempt_count=0,
                    claimed_at=now,
                )
                session.add(delivery)
                await session.flush()
                session.add(
                    OutboxEvent(
                        event_type=REMINDER_EVENT_TYPE,
                        aggregate_type="reminder",
                        aggregate_id=str(reminder.id),
                        recipient_telegram_id=owner.telegram_user_id,
                        payload={
                            "reminder_id": reminder.id,
                            "delivery_id": delivery.id,
                            "text": reminder.text,
                        },
                        status=OutboxEventStatus.PENDING.value,
                        available_at=now,
                    )
                )
                reminder.status = ReminderStatus.PAUSED.value
                claimed += 1
        if claimed:
            logger.info("Reminder claim completed | count=%s", claimed)
        return claimed

    async def reclaim_stale(self) -> int:
        """Recover deliveries stuck by a crash older than the lease."""
        cutoff = self._clock.now_utc() - self._lease
        recovered = 0
        async with self._session_factory.begin() as session:
            stale = (
                (
                    await session.execute(
                        select(ReminderDelivery)
                        .where(
                            ReminderDelivery.status.in_(
                                [
                                    ReminderDeliveryStatus.CLAIMED.value,
                                    ReminderDeliveryStatus.SENDING.value,
                                ]
                            ),
                            ReminderDelivery.claimed_at <= cutoff,
                        )
                        .limit(self._batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for delivery in stale:
                event = (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.event_type == REMINDER_EVENT_TYPE,
                            OutboxEvent.aggregate_id == str(delivery.reminder_id),
                        )
                    )
                ).scalar_one_or_none()
                if event is None:
                    event = (
                        await session.execute(
                            select(OutboxEvent)
                            .where(OutboxEvent.event_type == REMINDER_EVENT_TYPE)
                            .where(OutboxEvent.payload["delivery_id"].as_integer() == delivery.id)
                        )
                    ).scalar_one_or_none()
                if event is not None and event.status == OutboxEventStatus.SENT.value:
                    delivery.status = ReminderDeliveryStatus.SENT.value
                    delivery.sent_at = event.sent_at
                elif event is not None and event.status == OutboxEventStatus.PENDING.value:
                    continue  # the consumer will still process this event
                else:
                    delivery.status = ReminderDeliveryStatus.RETRY_PENDING.value
                recovered += 1
        if recovered:
            logger.info("Reminder stale-lease recovery completed | count=%s", recovered)
        return recovered
