"""Thin Telegram handlers delegating to application services."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from sqlalchemy.exc import SQLAlchemyError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from office_assistant.application.authentication import (
    AuthenticationResult,
    AuthenticationService,
    TelegramIdentity,
)
from office_assistant.application.errors import ApplicationError, AuthorizationDenied
from office_assistant.application.file_validation import validate_upload
from office_assistant.application.ingestion import IngestionSessionService, UploadMetadata
from office_assistant.application.user_management import UserManagementService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.infrastructure.storage.local import LocalFileStorage, StorageError
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.state import PasswordAttemptLimiter, PasswordEntryState

SERVICES_KEY = "phase2_services"
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TelegramServices:
    authentication: AuthenticationService
    users: UserManagementService
    password_state: PasswordEntryState
    limiter: PasswordAttemptLimiter
    ingestion: IngestionSessionService | None = None
    storage: LocalFileStorage | None = None


def _services(context: ContextTypes.DEFAULT_TYPE) -> TelegramServices:
    return cast(TelegramServices, context.bot_data[SERVICES_KEY])


def _is_private(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type == ChatType.PRIVATE


def _identity(update: Update) -> TelegramIdentity | None:
    user = update.effective_user
    if user is None:
        return None
    return TelegramIdentity(user.id, user.username, user.first_name, user.last_name)


def _status_message(user: User) -> str:
    if user.status == UserStatus.ACTIVE:
        return messages.WELCOME
    if user.status == UserStatus.PENDING:
        return messages.PENDING_STATUS
    if user.status == UserStatus.REJECTED:
        return messages.AUTH_REJECTED
    return messages.AUTH_DISABLED


def _user_label(user: User) -> str:
    name = " ".join(part for part in (user.first_name, user.last_name) if part)
    username = f"@{user.telegram_username}" if user.telegram_username else "username yoxdur"
    return f"ID: {user.telegram_user_id} | {name or 'Ad yoxdur'} | {username} | {user.status}"


def _user_keyboard(user: User) -> InlineKeyboardMarkup | None:
    if user.status == UserStatus.PENDING:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(messages.APPROVE, callback_data=f"ua:approve:{user.id}"),
                    InlineKeyboardButton(messages.REJECT, callback_data=f"ua:reject:{user.id}"),
                ]
            ]
        )
    if user.status == UserStatus.ACTIVE and user.role == UserRole.USER:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(messages.DISABLE, callback_data=f"ua:disable:{user.id}")]]
        )
    return None


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    identity = _identity(update)
    message = update.effective_message
    if identity is None or message is None:
        return
    services = _services(context)
    try:
        user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    except SQLAlchemyError:
        logger.exception("Telegram start database lookup failed")
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    if user is None:
        services.password_state.request(identity.telegram_user_id)
        await message.reply_text(messages.AUTH_PROMPT)
        return
    if user.status == UserStatus.ACTIVE:
        await services.users.touch_active_user(identity.telegram_user_id)
    await message.reply_text(_status_message(user))


async def handle_password_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    identity = _identity(update)
    message = update.effective_message
    if identity is None or message is None or not message.text:
        return
    services = _services(context)
    if not services.password_state.contains(identity.telegram_user_id):
        try:
            user = await services.users.get_by_telegram_id(identity.telegram_user_id)
            if user is None:
                await message.reply_text(messages.UNKNOWN_USER)
            elif user.status != UserStatus.ACTIVE:
                await message.reply_text(_status_message(user))
            elif services.ingestion is not None:
                added = await services.ingestion.add_instruction(
                    user,
                    text_content=message.text,
                    chat_id=update.effective_chat.id if update.effective_chat else 0,
                    message_id=message.message_id,
                    update_id=update.update_id,
                )
                await message.reply_text(
                    messages.INSTRUCTION_ADDED if added else messages.NO_OPEN_SESSION
                )
            else:
                await message.reply_text(messages.NO_OPEN_SESSION)
        except (ApplicationError, SQLAlchemyError):
            await message.reply_text(messages.TEMPORARY_ERROR)
        return
    if services.limiter.is_blocked(identity.telegram_user_id):
        await message.reply_text(messages.AUTH_WAIT)
        return

    try:
        result = await services.authentication.authenticate(identity, message.text)
        if result is None:
            services.limiter.register_failure(identity.telegram_user_id)
            await message.reply_text(messages.AUTH_FAILED)
            return
        services.password_state.clear(identity.telegram_user_id)
        services.limiter.clear(identity.telegram_user_id)
        await _send_auth_result(update, context, result)
    except ApplicationError:
        await message.reply_text(messages.TEMPORARY_ERROR)
    except SQLAlchemyError:
        logger.exception("Telegram authentication database operation failed")
        await message.reply_text(messages.TEMPORARY_ERROR)
    finally:
        try:
            await message.delete()
        except Exception:
            pass


async def _send_auth_result(
    update: Update, context: ContextTypes.DEFAULT_TYPE, result: AuthenticationResult
) -> None:
    message = update.effective_message
    if message is not None:
        if result.outcome == "admin_created":
            await message.reply_text(messages.AUTH_ADMIN)
        else:
            await message.reply_text(messages.AUTH_PENDING)
    if result.outcome == "pending":
        for admin_id in result.admin_telegram_ids:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"Yeni giriş müraciəti:\n{_user_label(result.user)}",
                    reply_markup=_user_keyboard(result.user),
                )
            except Exception:
                continue


async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    identity = _identity(update)
    message = update.effective_message
    if identity is None or message is None:
        return
    services = _services(context)
    services.password_state.clear(identity.telegram_user_id)
    user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if user is not None and user.status == UserStatus.ACTIVE and services.ingestion is not None:
        try:
            cancelled = await services.ingestion.cancel(user)
        except (ApplicationError, SQLAlchemyError):
            await message.reply_text(messages.TEMPORARY_ERROR)
            return
        await message.reply_text(
            messages.CANCEL_SUCCESS if cancelled else messages.CANCEL_NO_SESSION
        )
        return
    await message.reply_text(messages.CANCELLED)


async def handle_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    identity = _identity(update)
    message = update.effective_message
    services = _services(context)
    if identity is None or message is None or services.ingestion is None:
        return
    user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if user is None:
        await message.reply_text(messages.UNKNOWN_USER)
        return
    if user.status != UserStatus.ACTIVE:
        await message.reply_text(_status_message(user))
        return
    try:
        summary = await services.ingestion.commit(user)
    except (ApplicationError, SQLAlchemyError, StorageError):
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    if summary is None:
        await message.reply_text(messages.SAVE_NO_SESSION)
        return
    await message.reply_text(
        messages.SAVE_SUCCESS.format(
            files=summary.file_count,
            instructions=summary.instruction_count,
            duplicates=summary.duplicate_count,
        )
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    document = message.document
    if document is None:
        return
    await _handle_upload(
        update,
        context,
        file_id=document.file_id,
        filename=document.file_name or "upload.bin",
        content_type=document.mime_type,
        known_size=document.file_size,
        caption=message.caption,
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.photo:
        return
    photo = message.photo[-1]
    await _handle_upload(
        update,
        context,
        file_id=photo.file_id,
        filename=f"telegram_photo_{message.message_id}.jpg",
        content_type="image/jpeg",
        known_size=None,
        caption=message.caption,
    )


async def _handle_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    file_id: str,
    filename: str,
    content_type: str | None,
    known_size: int | None,
    caption: str | None,
) -> None:
    if not _is_private(update):
        return
    identity = _identity(update)
    message = update.effective_message
    services = _services(context)
    if (
        identity is None
        or message is None
        or services.storage is None
        or services.ingestion is None
    ):
        return
    user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if user is None:
        await message.reply_text(messages.UNKNOWN_USER)
        return
    if user.status != UserStatus.ACTIVE:
        await message.reply_text(_status_message(user))
        return
    max_bytes = services.ingestion.max_upload_bytes
    try:
        normalized_type = validate_upload(filename, content_type, known_size or 0, max_bytes)
    except ValueError as error:
        await message.reply_text(
            messages.FILE_TOO_LARGE if str(error) == "too_large" else messages.FILE_UNSUPPORTED
        )
        return
    staging_path, staging_key = services.storage.create_staging_path(Path(filename).suffix)
    try:
        telegram_file = await context.bot.get_file(file_id)
        await telegram_file.download_to_drive(custom_path=staging_path)
        actual_size = services.storage.size(staging_path)
        if actual_size > max_bytes:
            services.storage.delete(staging_key)
            await message.reply_text(messages.FILE_TOO_LARGE)
            return
        result = await services.ingestion.add_file(
            user,
            UploadMetadata(
                original_filename=filename,
                content_type=normalized_type,
                staging_path=staging_path,
                staging_key=staging_key,
                telegram_chat_id=update.effective_chat.id if update.effective_chat else 0,
                telegram_message_id=message.message_id,
                telegram_update_id=update.update_id,
                caption=caption,
            ),
        )
    except (ApplicationError, SQLAlchemyError, StorageError):
        services.storage.delete(staging_key)
        await message.reply_text(messages.STORAGE_ERROR)
        return
    await message.reply_text(
        messages.FILE_ADDED.format(
            files=result.summary.file_count,
            instructions=result.summary.instruction_count,
        )
        + (messages.FILE_DUPLICATE if result.duplicate else "")
    )


async def handle_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    identity = _identity(update)
    message = update.effective_message
    if identity is None or message is None:
        return
    try:
        user = await _services(context).users.get_by_telegram_id(identity.telegram_user_id)
    except SQLAlchemyError:
        logger.exception("Telegram whoami database lookup failed")
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    if user is None:
        await message.reply_text(messages.WHOAMI_UNKNOWN)
        return
    await message.reply_text(
        f"Telegram ID: {user.telegram_user_id}\nRol: {user.role}\nStatus: {user.status}"
    )


async def handle_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    identity = _identity(update)
    message = update.effective_message
    if identity is None or message is None:
        return
    try:
        users = await _services(context).users.list_users_for_actor(identity.telegram_user_id)
    except AuthorizationDenied:
        await message.reply_text(messages.NO_PERMISSION)
        return
    except SQLAlchemyError:
        logger.exception("Telegram users database lookup failed")
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    if not users:
        await message.reply_text("Qeydiyyatda istifadəçi yoxdur.")
        return
    for user in users:
        await message.reply_text(_user_label(user), reply_markup=_user_keyboard(user))


async def handle_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not _is_private(update):
        return
    await query.answer()
    actor = update.effective_user
    data = query.data or ""
    parts = data.split(":")
    if (
        actor is None
        or len(parts) != 3
        or parts[0] != "ua"
        or parts[1]
        not in {
            "approve",
            "reject",
            "disable",
        }
    ):
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    try:
        target_id = int(parts[2])
        service = _services(context).users
        if parts[1] == "approve":
            target = await service.approve(actor.id, target_id)
            response = messages.AUTH_APPROVED
        elif parts[1] == "reject":
            target = await service.reject(actor.id, target_id)
            response = messages.AUTH_REJECTED_BY_ADMIN
        else:
            target = await service.disable(actor.id, target_id)
            response = messages.AUTH_DISABLED
        await query.edit_message_text(f"{response}\n{_user_label(target)}")
        if parts[1] in {"approve", "reject"}:
            try:
                await context.bot.send_message(chat_id=target.telegram_user_id, text=response)
            except Exception:
                pass
    except (AuthorizationDenied, ApplicationError, ValueError):
        await query.edit_message_text(messages.NO_PERMISSION)
    except SQLAlchemyError:
        logger.exception("Telegram user callback database operation failed")
        await query.edit_message_text(messages.TEMPORARY_ERROR)
