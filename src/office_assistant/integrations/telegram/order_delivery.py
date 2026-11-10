"""Two-phase order sends: persist intent before Telegram, never replay an uncertain send.

The event lock is acquired before the order and ledger locks. Lifecycle mutations take
only order then ledger locks. Holding the order lock across the bounded Telegram call
serializes delivery with freeze/reschedule: a stop acknowledged first prevents the send.
"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Bot
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError

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
from office_assistant.integrations.telegram import orders_format
from office_assistant.orders.dispatch import record_attempt

logger = logging.getLogger(__name__)
MAX_ORDER_SEND_ATTEMPTS = 3
_SENDABLE = (OrderReminderStatus.CLAIMED.value, OrderReminderStatus.RETRY_PENDING.value)


async def _locked_records(
    session: AsyncSession, event: OutboxEvent
) -> tuple[Order | None, OrderReminder | None]:
    # The generated payload contains IDs only. Malformed persisted data fails closed.
    try:
        order_id = int(str(event.payload.get("order_id", "")))
        entry_id = int(str(event.payload.get("reminder_id", "")))
    except (TypeError, ValueError):
        return None, None
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    entry = await session.scalar(
        select(OrderReminder).where(OrderReminder.id == entry_id).with_for_update()
    )
    if entry is not None and entry.order_id != order_id:
        return None, None
    return order, entry


async def _eligible(
    session: AsyncSession,
    event: OutboxEvent,
    order: Order | None,
    entry: OrderReminder | None,
    *,
    sending: bool = False,
) -> bool:
    if entry is None:
        event.status = OutboxEventStatus.FAILED.value
        event.last_error = "order reminder record missing or inconsistent"
        return False
    creator = None if order is None else await session.get(User, order.created_by_user_id)
    expected = (OrderReminderStatus.SENDING.value,) if sending else _SENDABLE
    if (
        order is None
        or order.state != OrderState.ACTIVE.value
        or entry.status not in expected
        or creator is None
        or creator.status != UserStatus.ACTIVE.value
        or creator.telegram_user_id != event.recipient_telegram_id
    ):
        if entry.status in (*_SENDABLE, OrderReminderStatus.SENDING.value):
            entry.status = OrderReminderStatus.CANCELLED.value
        event.status = OutboxEventStatus.CANCELLED.value
        event.last_error = "order, schedule or recipient no longer eligible"
        record_attempt(session, entry, event, "CANCELLED")
        return False
    return True


async def reserve_order_send(session: AsyncSession, event: OutboxEvent, clock: Clock) -> bool:
    """Caller commits SENDING and the started audit record before any network call."""

    order, entry = await _locked_records(session, event)
    if not await _eligible(session, event, order, entry):
        return False
    assert entry is not None
    if event.attempt_count >= MAX_ORDER_SEND_ATTEMPTS:
        event.status = OutboxEventStatus.FAILED.value
        entry.status = OrderReminderStatus.FAILED.value
        entry.last_error = event.last_error = "attempt limit reached"
        return False
    event.status = OutboxEventStatus.SENDING.value
    entry.status = OrderReminderStatus.SENDING.value
    entry.claimed_at = clock.now_utc()
    event.attempt_count += 1
    entry.attempt_count += 1
    record_attempt(session, entry, event, "STARTED")
    return True


def retry_policy(error: TelegramError, attempt: int) -> tuple[str, timedelta | None]:
    """Only definite rejections are retryable; transport failures may already have sent.

    ASSUMPTION (Q49): prioritize no duplicates over automatic recovery of ambiguous
    outcomes. Telegram sendMessage has no caller-supplied idempotency key.
    """

    if isinstance(error, (BadRequest, Forbidden)):
        return type(error).__name__, None  # BadRequest is also a NetworkError.
    if isinstance(error, NetworkError):
        return "DELIVERY_UNKNOWN", None
    if attempt >= MAX_ORDER_SEND_ATTEMPTS:
        return type(error).__name__, None
    delay = timedelta(seconds=5 * 2 ** (attempt - 1))
    if isinstance(error, RetryAfter):
        retry_after = error.retry_after
        delay = max(
            delay,
            retry_after if isinstance(retry_after, timedelta) else timedelta(seconds=retry_after),
        )
    return type(error).__name__, delay


async def deliver_reserved_order(
    bot: Bot, factory: async_sessionmaker[AsyncSession], event_id: int, clock: Clock
) -> None:
    """If this transaction crashes, SENDING remains durable for uncertain recovery."""

    async with factory.begin() as session:
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update()
        )
        if event is None or event.status != OutboxEventStatus.SENDING.value:
            return
        order, entry = await _locked_records(session, event)
        if not await _eligible(session, event, order, entry, sending=True):
            return
        assert order is not None and entry is not None
        text = orders_format.reminder_text(
            order,
            index=entry.ordinal + 1,
            total=order.warning_count or 0,
            now_utc=clock.now_utc(),
        )
        try:
            sent = await bot.send_message(
                chat_id=event.recipient_telegram_id,
                text=text,
                connect_timeout=10,
                read_timeout=20,
                write_timeout=20,
                pool_timeout=5,
            )
        except TelegramError as error:
            code, delay = retry_policy(error, event.attempt_count)
            # Never persist provider exception text (it may contain secrets or input).
            entry.last_error = event.last_error = code
            entry.status = (
                OrderReminderStatus.FAILED.value
                if delay is None
                else OrderReminderStatus.RETRY_PENDING.value
            )
            event.status = (
                OutboxEventStatus.FAILED.value if delay is None else OutboxEventStatus.PENDING.value
            )
            if delay is not None:
                event.available_at = clock.now_utc() + delay
            record_attempt(
                session, entry, event, "UNKNOWN" if code == "DELIVERY_UNKNOWN" else "FAILED"
            )
            logger.warning("Order delivery failed | event_id=%s code=%s", event.id, code)
        else:
            event.status = OutboxEventStatus.SENT.value
            entry.status = OrderReminderStatus.SENT.value
            entry.sent_at = event.sent_at = clock.now_utc()
            entry.last_error = event.last_error = None
            record_attempt(session, entry, event, "SENT", message_id=sent.message_id)
