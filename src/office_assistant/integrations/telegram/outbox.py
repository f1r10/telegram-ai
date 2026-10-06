"""Small PostgreSQL-backed Telegram notification consumer."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from office_assistant.infrastructure.database.models import OutboxEvent, OutboxEventStatus

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
        if event.event_type == "STRUCTURED_EXTRACTION_COMPLETED":
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
        else:
            text = (
                "Emal tamamlandı.\n\n"
                f"Fayllar: {payload.get('total', 0)}\n"
                f"Uğurlu: {payload.get('succeeded', 0)}\n"
                f"Deferred/OCR/vision: {payload.get('deferred', 0)}\n"
                f"Xəta: {payload.get('failed', 0)}"
            )
        try:
            await bot.send_message(
                chat_id=event.recipient_telegram_id, text=text, reply_markup=markup
            )
        except TelegramError as error:
            event.attempt_count += 1
            event.last_error = str(error)[:2000]
            if event.attempt_count >= 3:
                event.status = OutboxEventStatus.FAILED
            else:
                event.available_at = datetime.now(UTC) + timedelta(seconds=event.attempt_count * 5)
            logger.warning("Outbox notification failed | event_id=%s", event.id)
        else:
            event.status = OutboxEventStatus.SENT
            event.sent_at = datetime.now(UTC)
        return True
