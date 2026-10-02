"""python-telegram-bot application construction."""

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
    handle_cancel,
    handle_document,
    handle_password_text,
    handle_photo,
    handle_save,
    handle_start,
    handle_user_callback,
    handle_users,
    handle_whoami,
)


def create_telegram_application(
    token: str, services: TelegramServices
) -> Application[Any, Any, Any, Any, Any, Any]:
    """Build the polling application without contacting Telegram."""

    application = Application.builder().token(token).build()
    application.bot_data[SERVICES_KEY] = services
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("whoami", handle_whoami))
    application.add_handler(CommandHandler("users", handle_users))
    application.add_handler(CommandHandler("cancel", handle_cancel))
    application.add_handler(CommandHandler("saxla", handle_save))
    application.add_handler(CallbackQueryHandler(handle_user_callback, pattern=r"^ua:"))
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
