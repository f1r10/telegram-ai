"""python-telegram-bot application construction."""

# PTB's heavily generic builder types are intentionally kept at this adapter
# boundary; Telegram handler/service code remains explicitly typed.
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import asyncio
import logging
from typing import Any

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from office_assistant.integrations.telegram.handlers import (
    COMMAND_CALLBACKS,
    SERVICES_KEY,
    TelegramServices,
    handle_answer_navigation,
    handle_catalog_source_button,
    handle_catalog_product_add_callback,
    handle_delete_callback,
    handle_document,
    handle_error,
    handle_extraction_callback,
    handle_menu_callback,
    handle_menu_interruption,
    handle_password_text,
    handle_photo,
    handle_reminder_callback,
    handle_revoke_reference_callback,
    handle_sales_callback,
    handle_sales_import_callback,
    handle_source_button,
    handle_stock_callback,
    handle_user_callback,
)
from office_assistant.integrations.telegram.outbox import run_outbox_consumer
from office_assistant.integrations.telegram.update_processor import UserSerialUpdateProcessor
from office_assistant.knowledge.retrieval import KnowledgeRetrievalService

OUTBOX_TASK_KEY = "outbox_task"
OUTBOX_STOP_KEY = "outbox_stop"
logger = logging.getLogger(__name__)


async def _preflight_knowledge(retrieval: KnowledgeRetrievalService) -> None:
    """Load local embedding models and report the projection before users ask."""

    try:
        target = await retrieval.preflight()
    except Exception as error:
        logger.error("Knowledge preflight failed | error_type=%s", type(error).__name__)
        return
    logger.info("Knowledge projection ready | target=%s", target)


async def _post_init(application: Application[Any, Any, Any, Any, Any, Any]) -> None:
    services = application.bot_data[SERVICES_KEY]
    if not isinstance(services, TelegramServices):
        return
    if services.knowledge is not None:
        await _preflight_knowledge(services.knowledge)
    if services.session_factory is not None:
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
        .concurrent_updates(UserSerialUpdateProcessor(max_concurrent_updates=8))
        .connection_pool_size(32)
        .pool_timeout(10.0)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    application.bot_data[SERVICES_KEY] = services
    application.add_error_handler(handle_error)
    # Run before all command/photo/document handlers. A search scope chosen earlier
    # must not unexpectedly apply to a later message after another action.
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.COMMAND | filters.PHOTO | filters.Document.ALL),
            handle_menu_interruption,
        ),
        group=-1,
    )
    for name, callback in COMMAND_CALLBACKS.items():
        application.add_handler(CommandHandler(name, callback))
    application.add_handler(CallbackQueryHandler(handle_user_callback, pattern=r"^ua:"))
    application.add_handler(CallbackQueryHandler(handle_menu_callback, pattern=r"^om:"))
    application.add_handler(CallbackQueryHandler(handle_answer_navigation, pattern=r"^nav:"))
    application.add_handler(CallbackQueryHandler(handle_source_button, pattern=r"^src:"))
    application.add_handler(CallbackQueryHandler(handle_catalog_source_button, pattern=r"^cat:"))
    application.add_handler(CallbackQueryHandler(handle_catalog_product_add_callback, pattern=r"^pa:"))
    application.add_handler(CallbackQueryHandler(handle_sales_callback, pattern=r"^sl:"))
    application.add_handler(CallbackQueryHandler(handle_stock_callback, pattern=r"^st:"))
    application.add_handler(CallbackQueryHandler(handle_sales_import_callback, pattern=r"^si:"))
    application.add_handler(CallbackQueryHandler(handle_extraction_callback, pattern=r"^xr:"))
    application.add_handler(CallbackQueryHandler(handle_reminder_callback, pattern=r"^rp:"))
    application.add_handler(CallbackQueryHandler(handle_reminder_callback, pattern=r"^rh:"))
    application.add_handler(CallbackQueryHandler(handle_delete_callback, pattern=r"^rd:"))
    application.add_handler(CallbackQueryHandler(handle_revoke_reference_callback, pattern=r"^ri:"))
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
