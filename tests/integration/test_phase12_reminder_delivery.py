"""Phase 12 integration tests: atomic reminder claiming and the delivery ledger."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select
from telegram import Bot

from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    OutboxEvent,
    OutboxEventStatus,
    Reminder,
    ReminderDelivery,
    ReminderDeliveryStatus,
    ReminderStatus,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.outbox import (
    _send_one,  # pyright: ignore[reportPrivateUsage]
)
from office_assistant.reminders.dispatch import REMINDER_EVENT_TYPE, ReminderDispatchService

pytestmark = pytest.mark.integration

DUE_UTC = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)
CLAIM_UTC = datetime(2026, 10, 1, 5, 5, tzinfo=UTC)
RECLAIM_UTC = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
ABANDONED_AT = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now_utc(self) -> datetime:
        return self._now


def _settings_or_skip() -> Settings:
    settings = load_settings()
    if not all(
        (
            settings.postgres_db,
            settings.postgres_user,
            settings.postgres_password,
            settings.postgres_host,
        )
    ):
        pytest.skip("PostgreSQL integration settings are not configured")
    return settings


def _reminder(user_id: int, key: str, text: str, status: ReminderStatus) -> Reminder:
    return Reminder(
        user_id=user_id,
        text=text,
        timezone="Asia/Baku",
        due_at_utc=DUE_UTC,
        next_run_at=DUE_UTC,
        status=status.value,
        idempotency_key=key,
    )


def test_due_reminders_are_claimed_once_and_delivered_through_the_ledger() -> None:
    settings = _settings_or_skip()

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        try:
            async with factory.begin() as session:
                await session.execute(delete(ReminderDelivery))
                await session.execute(delete(OutboxEvent))
                await session.execute(delete(Reminder))
                await session.execute(delete(AuditEvent))
                await session.execute(delete(User))
                user = User(
                    telegram_user_id=941201,
                    role=UserRole.USER,
                    status=UserStatus.ACTIVE,
                    timezone="Asia/Baku",
                )
                session.add(user)
                await session.flush()
                session.add_all(
                    [
                        _reminder(
                            user.id, "phase12-claim-a", "Hesabatı göndərməyi", ReminderStatus.ACTIVE
                        ),
                        _reminder(
                            user.id, "phase12-claim-b", "Təklif göndərməyi", ReminderStatus.ACTIVE
                        ),
                        _reminder(user.id, "phase12-stale", "Zəng etməyi", ReminderStatus.PAUSED),
                    ]
                )

            clock = FixedClock(CLAIM_UTC)
            claims = await asyncio.gather(
                ReminderDispatchService(factory, clock=clock).claim_due(),
                ReminderDispatchService(factory, clock=clock).claim_due(),
            )
            assert sum(claims) == 2

            async with factory() as session:
                active = (
                    (
                        await session.execute(
                            select(Reminder).where(Reminder.status == ReminderStatus.ACTIVE.value)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert active == []
                events = (await session.execute(select(OutboxEvent))).scalars().all()
                assert len(events) == 2
                assert {event.event_type for event in events} == {REMINDER_EVENT_TYPE}
                deliveries = (
                    (await session.execute(select(ReminderDelivery).order_by(ReminderDelivery.id)))
                    .scalars()
                    .all()
                )
                assert [row.status for row in deliveries] == [
                    ReminderDeliveryStatus.CLAIMED.value,
                    ReminderDeliveryStatus.CLAIMED.value,
                ]
                assert {row.scheduled_for for row in deliveries} == {DUE_UTC}

            # A second pass can never produce a duplicate delivery for the same occurrence.
            assert await ReminderDispatchService(factory, clock=clock).claim_due() == 0

            bot = SimpleNamespace(
                send_message=AsyncMock(return_value=SimpleNamespace(message_id=55))
            )
            assert await _send_one(cast(Bot, bot), factory) is True
            assert await _send_one(cast(Bot, bot), factory) is True
            assert await _send_one(cast(Bot, bot), factory) is False

            send = cast(AsyncMock, bot.send_message)
            assert send.await_count == 2
            assert {str(call.kwargs["text"]) for call in send.await_args_list} == {
                messages.REMINDER_DELIVERED.format(text="Hesabatı göndərməyi"),
                messages.REMINDER_DELIVERED.format(text="Təklif göndərməyi"),
            }

            async with factory() as session:
                rows = (
                    (await session.execute(select(ReminderDelivery).order_by(ReminderDelivery.id)))
                    .scalars()
                    .all()
                )
                assert [row.status for row in rows] == [
                    ReminderDeliveryStatus.SENT.value,
                    ReminderDeliveryStatus.SENT.value,
                ]
                assert all(row.telegram_message_id == 55 for row in rows)
                assert all(row.sent_at is not None for row in rows)
                delivered = (
                    await session.execute(
                        select(Reminder).where(Reminder.idempotency_key == "phase12-claim-a")
                    )
                ).scalar_one()
                assert delivered.status == ReminderStatus.SENT.value

            # A send abandoned mid-flight is recovered once its lease expires.
            async with factory.begin() as session:
                abandoned = (
                    await session.execute(
                        select(Reminder).where(Reminder.idempotency_key == "phase12-stale")
                    )
                ).scalar_one()
                session.add(
                    ReminderDelivery(
                        reminder_id=abandoned.id,
                        scheduled_for=abandoned.due_at_utc,
                        status=ReminderDeliveryStatus.SENDING.value,
                        attempt_count=1,
                        claimed_at=ABANDONED_AT,
                    )
                )
                session.add(
                    OutboxEvent(
                        event_type=REMINDER_EVENT_TYPE,
                        aggregate_type="reminder",
                        aggregate_id=str(abandoned.id),
                        recipient_telegram_id=941201,
                        payload={"reminder_id": abandoned.id, "text": abandoned.text},
                        status=OutboxEventStatus.FAILED.value,
                        attempt_count=3,
                        available_at=CLAIM_UTC,
                    )
                )

            recovered = await ReminderDispatchService(
                factory, clock=FixedClock(RECLAIM_UTC), lease_seconds=60
            ).reclaim_stale()
            assert recovered == 1
            async with factory() as session:
                row = (
                    await session.execute(
                        select(ReminderDelivery).where(
                            ReminderDelivery.status == ReminderDeliveryStatus.RETRY_PENDING.value
                        )
                    )
                ).scalar_one()
                assert row.claimed_at == ABANDONED_AT
        finally:
            await engine.dispose()

    asyncio.run(scenario())
