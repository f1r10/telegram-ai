"""python-telegram-bot application construction."""

# PTB's heavily generic builder types are intentionally kept at this adapter
# boundary; Telegram handler/service code remains explicitly typed.
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import asyncio
from typing import Any

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from office_assistant.integrations.telegram.handlers import (
    SERVICES_KEY,
    TelegramServices,
    handle_baza,
    handle_cancel,
    handle_document,
    handle_error,
    handle_extraction_callback,
    handle_internet,
    handle_password_text,
    handle_photo,
    handle_save,
    handle_start,
    handle_tamaxtar,
    handle_user_callback,
    handle_users,
    handle_whoami,
)
from office_assistant.integrations.telegram.outbox import run_outbox_consumer

OUTBOX_TASK_KEY = "outbox_task"
OUTBOX_STOP_KEY = "outbox_stop"


async def _post_init(application: Application[Any, Any, Any, Any, Any, Any]) -> None:
    services = application.bot_data[SERVICES_KEY]
    if isinstance(services, TelegramServices) and services.session_factory is not None:
        stop = asyncio.Event()
        application.bot_data[OUTBOX_STOP_KEY] = stop
        application.bot_data[OUTBOX_TASK_KEY] = asyncio.create_task(
            run_outbox_consumer(application.bot, services.session_factory, stop)
        )


async def _post_shutdown(application: Application[Any, Any, Any, Any, Any, Any]) -> None:
    stop = application.bot_data.get(OUTBOX_STOP_KEY)
    task = application.bot_data.get(OUTBOX_TASK_KEY)
    if isinstance(stop, asyncio.Event):
        stop.set()
    if isinstance(task, asyncio.Task):
        task.cancel()
        await task


def create_telegram_application(
    token: str, services: TelegramServices
) -> Application[Any, Any, Any, Any, Any, Any]:
    """Build the polling application without contacting Telegram."""

    application = (
        Application.builder()
        .token(token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    application.bot_data[SERVICES_KEY] = services
    application.add_error_handler(handle_error)
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("whoami", handle_whoami))
    application.add_handler(CommandHandler("users", handle_users))
    application.add_handler(CommandHandler("cancel", handle_cancel))
    application.add_handler(CommandHandler("saxla", handle_save))
    application.add_handler(CommandHandler("baza", handle_baza))
    application.add_handler(CommandHandler("internet", handle_internet))
    application.add_handler(CommandHandler("tamaxtar", handle_tamaxtar))
    application.add_handler(CallbackQueryHandler(handle_user_callback, pattern=r"^ua:"))
    application.add_handler(CallbackQueryHandler(handle_extraction_callback, pattern=r"^xr:"))
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.Document.ALL, handle_document)
    )
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.PHOTO, handle_photo))
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, handle_password_text
        )
    )
    return application
