"""Small PostgreSQL-backed Telegram notification consumer."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from office_assistant.infrastructure.database.models import (
    OutboxEvent,
    OutboxEventStatus,
    Reminder,
    ReminderDelivery,
    ReminderDeliveryStatus,
    ReminderStatus,
)
from office_assistant.integrations.telegram import messages
from office_assistant.reminders.dispatch import REMINDER_EVENT_TYPE

logger = logging.getLogger(__name__)


async def run_outbox_consumer(
    bot: Bot, session_factory: async_sessionmaker[AsyncSession], stop: asyncio.Event
) -> None:
    while not stop.is_set():
        sent = await _send_one(bot, session_factory)
        if not sent:
            try:
                await asyncio.wait_for(stop.wait(), 2.0)
            except TimeoutError:
                pass


async def _send_one(bot: Bot, session_factory: async_sessionmaker[AsyncSession]) -> bool:
    async with session_factory.begin() as session:
        event = (
            await session.execute(
                select(OutboxEvent)
                .where(
                    OutboxEvent.status == OutboxEventStatus.PENDING,
                    OutboxEvent.available_at <= datetime.now(UTC),
                )
                .order_by(OutboxEvent.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if event is None:
            return False
        payload = event.payload
        markup = None
        delivery: ReminderDelivery | None = None
        reminder: Reminder | None = None
        if event.event_type == REMINDER_EVENT_TYPE:
            delivery_id = int(str(payload.get("delivery_id", 0)))
            delivery = await session.get(ReminderDelivery, delivery_id)
            if delivery is None:
                event.status = OutboxEventStatus.FAILED.value
                event.last_error = f"Reminder delivery row missing | delivery_id={delivery_id}"
                logger.warning("Reminder delivery ledger row absent | event_id=%s", event.id)
                return True
            reminder = await session.get(Reminder, delivery.reminder_id)
            text = messages.REMINDER_DELIVERED.format(text=payload.get("text", ""))
            delivery.status = ReminderDeliveryStatus.SENDING.value
        elif event.event_type == "STRUCTURED_EXTRACTION_COMPLETED":
            counts = payload.get("counts", {})
            counts = cast(dict[str, object], counts) if isinstance(counts, dict) else {}
            text = (
                "Strukturlaşdırılmış analiz tamamlandı.\n\n"
                f"Məhsullar: {counts.get('PRODUCT', 0)}\n"
                f"Qiymətlər: {counts.get('PRICE', 0)}\n"
                f"Müştərilər: {counts.get('CUSTOMER', 0)}\n"
                f"Təkliflər: {counts.get('PROPOSAL', 0)}\n\n"
                f"Diqqət tələb edən: {payload.get('problems', 0)}\n\n"
                "Məlumatlar hələ biznes bazasına yazılmayıb."
            )
            run_id = str(payload.get("run_id", ""))
            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Yaddaşa yaz", callback_data=f"xr:a:{run_id}"),
                        InlineKeyboardButton("Problemlərə bax", callback_data=f"xr:p:{run_id}"),
                        InlineKeyboardButton("Ləğv et", callback_data=f"xr:r:{run_id}"),
                    ]
                ]
            )
        elif event.event_type == "KNOWLEDGE_INDEX_COMPLETED":
            text = messages.KNOWLEDGE_INDEX_COMPLETED.format(
                files=payload.get("files_indexed", 0),
                chunks=payload.get("chunks_indexed", 0),
                failed=payload.get("files_failed", 0),
            )
            if payload.get("status") != "SUCCEEDED":
                text += (
                    "\nBəzi faylların axtarış indeksi hazır deyil. "
                    "Oxunmayan fayllar üçün yenidən emal və ya OCR tələb oluna bilər."
                )
        else:
            text = messages.INGESTION_COMPLETED.format(
                total=payload.get("total", 0),
                succeeded=payload.get("succeeded", 0),
                deferred=payload.get("deferred", 0),
                failed=payload.get("failed", 0),
            )
            if payload.get("succeeded", 0):
                text += "\nAxtarış indeksinin hazırlanması davam edir."
            else:
                text += "\nAxtarış üçün oxuna bilən mətn tapılmadı."

        try:
            sent = await bot.send_message(
                chat_id=event.recipient_telegram_id, text=text, reply_markup=markup
            )
        except TelegramError as error:
            event.attempt_count += 1
            event.last_error = str(error)[:2000]
            terminal = event.attempt_count >= 3
            if terminal:
                event.status = OutboxEventStatus.FAILED.value
            else:
                event.available_at = datetime.now(UTC) + timedelta(seconds=event.attempt_count * 5)
            if delivery is not None:
                delivery.attempt_count += 1
                delivery.last_error = event.last_error
                delivery.status = (
                    ReminderDeliveryStatus.FAILED.value
                    if terminal
                    else ReminderDeliveryStatus.RETRY_PENDING.value
                )
            logger.warning("Outbox notification failed | event_id=%s", event.id)
        else:
            event.status = OutboxEventStatus.SENT.value
            event.sent_at = datetime.now(UTC)
            if delivery is not None and reminder is not None:
                delivery.status = ReminderDeliveryStatus.SENT.value
                delivery.sent_at = event.sent_at
                delivery.telegram_message_id = sent.message_id
                reminder.status = ReminderStatus.SENT.value
        return True
