"""Thin Telegram handlers delegating to application services."""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    Update,
)
from telegram.constants import ChatAction, ChatType
from telegram.error import NetworkError, TelegramError
from telegram.ext import ContextTypes

from office_assistant.ai.service import (
    AIServiceError,
    AnswerGenerationUnavailable,
    GroundedAnswerService,
    InProcessRateGuard,
    RetrievalUnavailable,
)
from office_assistant.application.authentication import (
    AuthenticationResult,
    AuthenticationService,
    TelegramIdentity,
)
from office_assistant.application.errors import (
    ApplicationError,
    AuthorizationDenied,
    DomainConflict,
)
from office_assistant.application.file_validation import validate_upload
from office_assistant.application.ingestion import IngestionSessionService, UploadMetadata
from office_assistant.application.reference_images import (
    ReferenceImageDenialReason,
    ReferenceImageResult,
    ReferenceImageSaveDenied,
    ReferenceImageService,
    ReferenceImageStatus,
)
from office_assistant.application.reminders import ReminderProposal, ReminderService
from office_assistant.application.user_management import UserManagementService
from office_assistant.business.reference_confirmation import (
    AmbiguousPictureConfirmation,
    ProductPictureConfirmation,
    parse_reference_confirmation,
)
from office_assistant.extraction.review import ExtractionReviewService
from office_assistant.infrastructure.database.models import (
    Reminder,
    ReminderStatus,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.storage.local import LocalFileStorage, StorageError
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.formatting import split_telegram_text
from office_assistant.integrations.telegram.reminder_format import when_azerbaijani
from office_assistant.integrations.telegram.state import (
    DeleteConfirmationState,
    PasswordAttemptLimiter,
    PasswordEntryState,
    ReminderEditState,
)
from office_assistant.integrations.telegram.turn_context import (
    MediaRef,
    build_turn_context,
)
from office_assistant.internal_evidence.delivery import (
    FileDeliveryAmbiguous,
    FileDeliveryError,
    SourceFileDeliveryService,
    parse_file_delivery_request,
    parse_web_source_request,
)
from office_assistant.internal_evidence.models import ImageReading, InternalAnswer
from office_assistant.internal_evidence.service import InternalEvidenceService
from office_assistant.knowledge.retrieval import KnowledgeRetrievalService
from office_assistant.processing.vision import ImageVisionExtractor
from office_assistant.reminders.time_parsing import looks_like_reminder_request
from office_assistant.web_evidence.models import WebAnswer, WebEvidenceUnavailable
from office_assistant.web_evidence.service import WebEvidenceService

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
    session_factory: async_sessionmaker[AsyncSession] | None = None
    ai: GroundedAnswerService | None = None
    rate_guard: InProcessRateGuard | None = None
    telegram_message_limit: int = 4096
    extraction_reviews: ExtractionReviewService | None = None
    internal_evidence: InternalEvidenceService | None = None
    knowledge: KnowledgeRetrievalService | None = None
    file_delivery: SourceFileDeliveryService | None = None
    web_evidence: WebEvidenceService | None = None
    image_reader: ImageVisionExtractor | None = None
    reminders: ReminderService | None = None
    reference_images: ReferenceImageService | None = None
    reminder_edit_state: ReminderEditState = field(default_factory=ReminderEditState)
    delete_confirmation_state: DeleteConfirmationState = field(
        default_factory=DeleteConfirmationState
    )


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


def _command_text(message: Message) -> str:
    return message.text or message.caption or ""


# Commands whose attached picture is a search parameter rather than an upload (Master Spec §22).
MEDIA_COMMANDS = frozenset({"baza", "tamaxtar"})
# Commands that must store the file first, then run with it in the pending session.
INGEST_THEN_RUN_COMMANDS = frozenset({"saxla"})


def _caption_command(message: Message) -> tuple[str, str] | None:
    """Split a media caption into (command, arguments) only for known commands.

    ``CommandHandler`` never sees captions because captions leave ``Message.text`` empty, so this
    is the router's way of letting an explicit command beat file ingestion. Unknown ``/...``
    captions stay uploads instead of being dropped.
    """
    caption = message.caption
    entities = message.caption_entities
    if not caption or not entities:
        return None
    first = entities[0]
    if first.type != MessageEntity.BOT_COMMAND or first.offset != 0:
        return None
    name = caption[1 : first.length].split("@", 1)[0].casefold()
    if name not in COMMAND_CALLBACKS:
        return None
    return name, caption[first.length :].strip()


async def _route_caption_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Route an explicit caption command, keeping the attachment when the command uses it.

    Returning ``True`` means the caller must not ingest the file. ``False`` means the file is the
    point of the turn, so the caller stores it first and then runs the command.
    """
    message = update.effective_message
    if message is None:
        return False
    command = _caption_command(message)
    if command is None:
        return False
    name = command[0]
    if name in MEDIA_COMMANDS:
        # The picture is a query parameter: the handler reads it from this same message.
        logger.info("Caption command routed with media | command=%s media_attached=true", name)
        await COMMAND_CALLBACKS[name](update, context)
        return True
    if name in INGEST_THEN_RUN_COMMANDS:
        logger.info("Caption command will run after ingestion | command=%s", name)
        return False
    logger.info("Caption command routed | command=%s ingestion_skipped=true", name)
    await message.reply_text(messages.CAPTION_COMMAND_FILE_SKIPPED)
    await COMMAND_CALLBACKS[name](update, context)
    return True


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


async def _reply_long(message: object, text: str, limit: int) -> None:
    for part in split_telegram_text(text, limit=limit):
        for attempt in range(3):
            try:
                await message.reply_text(part)  # type: ignore[attr-defined]
                break
            except NetworkError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)


async def _send_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    # A cosmetic chat action must never prevent answering the user's question.
    try:
        await context.bot.send_chat_action(
            chat_id=chat_id,
            action=ChatAction.TYPING,
            connect_timeout=3,
            read_timeout=3,
            write_timeout=3,
            pool_timeout=3,
        )
    except TelegramError as error:
        logger.warning("Telegram typing action skipped | error_type=%s", type(error).__name__)


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    logger.error(
        "Telegram operation failed | error_type=%s",
        type(error).__name__,
        exc_info=(type(error), error, error.__traceback__) if error is not None else None,
    )
    if isinstance(update, Update) and _is_private(update) and update.effective_message:
        try:
            await _reply_long(update.effective_message, messages.TEMPORARY_ERROR, 4096)
        except TelegramError as error:
            logger.warning("Telegram error reply failed | error_type=%s", type(error).__name__)


async def _read_turn_images(
    context: ContextTypes.DEFAULT_TYPE,
    services: TelegramServices,
    images: tuple[MediaRef, ...],
    *,
    user_id: int,
) -> list[ImageReading]:
    """Read only the pictures this turn carries, and only for this question.

    A picture that cannot be read is reported to the user and skipped; it never becomes an
    invented transcription, a stored file or a business fact (ADR 0017, invariant 8).
    """
    readings: list[ImageReading] = []
    for item in images:
        if services.image_reader is None:
            logger.warning("Image reading is not configured | file_id=%s", item.file_id)
            continue
        try:
            telegram_file = await context.bot.get_file(item.file_id)
            body = bytes(await telegram_file.download_as_bytearray())
            transcription = await services.image_reader.read(
                image_bytes=body, media_type=item.media_type, user_id=user_id
            )
        except Exception as error:
            # Reading the picture is an enhancement on top of the text question, so the failure net
            # is deliberately wide: nothing here may cost the user the answer they asked for.
            logger.warning(
                "Image reading unavailable, turn continues without it | file_id=%s "
                "size_bytes=%s error_type=%s",
                item.file_id,
                item.byte_size,
                type(error).__name__,
            )
            continue
        if transcription.is_empty:
            logger.warning("Image read returned no content | file_id=%s", item.file_id)
            continue
        readings.append(
            ImageReading(
                filename=item.filename,
                text=transcription.text or transcription.description,
                description=transcription.description if transcription.text else "",
                model=transcription.model,
            )
        )
    return readings


def _with_image_notice(
    answer: str, images: tuple[MediaRef, ...], readings: list[ImageReading]
) -> str:
    """Tell the user, in Azerbaijani, when a picture they attached could not be read."""

    if not images or len(readings) >= len(images):
        return answer
    return f"{messages.IMAGE_NOT_READ}\n\n{answer}"


def _format_internal(result: InternalAnswer) -> str:
    if not result.citations:
        return result.answer
    source_lines = [f"[{item.number}] {item.label}" for item in result.citations]
    return f"{result.answer}\n\nMənbələr:\n" + "\n".join(source_lines)


def _format_web(result: WebAnswer) -> str:
    if not result.citations:
        return result.answer
    source_lines = [f"[{item.number}] {item.label}" for item in result.citations]
    return f"{result.answer}\n\nMənbələr:\n" + "\n".join(source_lines)


async def _try_file_delivery(
    message: object,
    services: TelegramServices,
    actor_telegram_user_id: int,
    text: str,
) -> bool:
    web_requested = parse_web_source_request(text)
    if web_requested is not False:
        if services.file_delivery is None:
            await message.reply_text(messages.WEB_SOURCE_UNAVAILABLE)  # type: ignore[attr-defined]
            return True
        try:
            item = await services.file_delivery.resolve_recent_url(
                actor_telegram_user_id,
                web_requested
                if isinstance(web_requested, int) and not isinstance(web_requested, bool)
                else None,
            )
            await message.reply_text(f"{item.title}\n{item.url}")  # type: ignore[attr-defined]
        except FileDeliveryAmbiguous:
            await message.reply_text(messages.SOURCE_FILE_AMBIGUOUS)  # type: ignore[attr-defined]
        except (FileDeliveryError, StorageError):
            await message.reply_text(messages.WEB_SOURCE_UNAVAILABLE)  # type: ignore[attr-defined]
        return True
    requested = parse_file_delivery_request(text)
    if requested is False:
        return False
    if services.file_delivery is None:
        await message.reply_text(messages.SOURCE_FILE_UNAVAILABLE)  # type: ignore[attr-defined]
        return True
    try:
        item = await services.file_delivery.resolve_recent(
            actor_telegram_user_id,
            requested if isinstance(requested, int) and not isinstance(requested, bool) else None,
        )
        with item.path.open("rb") as stream:
            await message.reply_document(  # type: ignore[attr-defined]
                document=stream,
                filename=item.filename,
                read_timeout=60,
                write_timeout=60,
            )
    except FileDeliveryAmbiguous:
        await message.reply_text(messages.SOURCE_FILE_AMBIGUOUS)  # type: ignore[attr-defined]
    except (FileDeliveryError, StorageError):
        await message.reply_text(messages.SOURCE_FILE_UNAVAILABLE)  # type: ignore[attr-defined]
    return True


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
            elif await _try_reminder_edit(message, services, user, identity.telegram_user_id):
                return
            elif services.ingestion is not None and await services.ingestion.has_open_session(user):
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
            elif await _try_file_delivery(
                message, services, identity.telegram_user_id, message.text
            ):
                return
            elif await _try_reminder_proposal(message, services, user, message.text):
                return
            elif services.internal_evidence is not None and services.rate_guard is not None:
                if not services.rate_guard.allow(user.id):
                    await message.reply_text(messages.AI_RATE_LIMITED)
                    return
                await _send_typing(context, message.chat_id)
                # A picture the user replies to is a first-class parameter of the question.
                turn = build_turn_context(message, text=message.text)
                images = turn.images
                readings = await _read_turn_images(context, services, images, user_id=user.id)
                result = await services.internal_evidence.answer(
                    user, message.text, image_readings=readings
                )
                await _reply_long(
                    message,
                    _with_image_notice(_format_internal(result), images, readings),
                    services.telegram_message_limit,
                )
            elif services.ai is not None and services.rate_guard is not None:
                if not services.rate_guard.allow(user.id):
                    await message.reply_text(messages.AI_RATE_LIMITED)
                    return
                await _send_typing(context, message.chat_id)
                result = await services.ai.answer_general(user.id, message.text)
                await _reply_long(message, result.answer, services.telegram_message_limit)
            elif services.ai is None:
                await message.reply_text(messages.AI_NOT_CONFIGURED)
            else:
                await message.reply_text(messages.NO_OPEN_SESSION)
        except AnswerGenerationUnavailable:
            await _reply_long(message, messages.AI_UNAVAILABLE, services.telegram_message_limit)
        except RetrievalUnavailable:
            logger.exception("Free-text retrieval projection failed")
            await message.reply_text(messages.QDRANT_UNAVAILABLE)
        except (ApplicationError, SQLAlchemyError, AIServiceError) as error:
            logger.error("Telegram message failed | error_type=%s", type(error).__name__)
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
    trailing_text = _command_text(message).partition(" ")[2].strip()
    try:
        if trailing_text:
            await services.ingestion.add_instruction(
                user,
                text_content=trailing_text,
                chat_id=message.chat_id,
                message_id=message.message_id,
                update_id=update.update_id,
                create_session=True,
            )
        summary = await services.ingestion.commit(user)
    except ReferenceImageSaveDenied as denied:
        # The session rolled back with the refused picture, so say which claim blocked the save.
        await message.reply_text(_reference_image_denied(denied))
        return
    except (ApplicationError, SQLAlchemyError, StorageError):
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    if summary is None:
        await message.reply_text(messages.SAVE_NO_SESSION)
        return
    reply = messages.SAVE_SUCCESS.format(
        files=summary.file_count,
        instructions=summary.instruction_count,
        duplicates=summary.duplicate_count,
    )
    if summary.reference_images_linked:
        reply += messages.REFERENCE_IMAGE_LINKED_NOTE.format(count=summary.reference_images_linked)
    await message.reply_text(reply)


def _reference_image_denied(denied: ReferenceImageSaveDenied) -> str:
    """Name the queued picture that stopped /saxla, and the claim that outranked it."""

    if denied.reason is ReferenceImageDenialReason.CONFLICT:
        return messages.REFERENCE_IMAGE_SAVE_CONFLICT.format(
            code=denied.holder_code or denied.canonical_code
        )
    return messages.REFERENCE_IMAGE_UNREADABLE


def _proposal_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    messages.REMINDER_CONFIRM_BUTTON, callback_data=f"rp:ok:{token}"
                ),
                InlineKeyboardButton(messages.REMINDER_EDIT_BUTTON, callback_data=f"rp:e:{token}"),
                InlineKeyboardButton(
                    messages.REMINDER_DISCARD_BUTTON, callback_data=f"rp:n:{token}"
                ),
            ]
        ]
    )


def _proposal_text(proposal: ReminderProposal) -> str:
    text = messages.REMINDER_PROPOSED.format(
        text=proposal.reminder_text,
        when=when_azerbaijani(proposal.due_at_utc, proposal.timezone_name),
    )
    if proposal.needs_clarification and proposal.clarification_reason:
        text += messages.REMINDER_CLARIFY_NOTE.format(reason=proposal.clarification_reason)
    return text


async def _show_proposal(message: Message, proposal: ReminderProposal) -> None:
    await message.reply_text(
        _proposal_text(proposal), reply_markup=_proposal_keyboard(proposal.token)
    )


async def _try_reminder_proposal(
    message: Message, services: TelegramServices, user: User, text: str
) -> bool:
    """Deterministic reminder rule (SPEC §6): interpret and ask, never save yet."""
    if services.reminders is None or not looks_like_reminder_request(text):
        return False
    proposal = services.reminders.propose(user, text)
    if proposal is None:
        await message.reply_text(messages.REMINDER_TIME_MISSING)
        return True
    await _show_proposal(message, proposal)
    return True


async def _try_reminder_edit(
    message: Message, services: TelegramServices, user: User, telegram_user_id: int
) -> bool:
    """Consume the next text message as a replacement time after the Edit button."""
    if services.reminders is None:
        return False
    token = services.reminder_edit_state.token_for(telegram_user_id)
    if token is None:
        return False
    services.reminder_edit_state.clear(telegram_user_id)
    if services.reminders.pending.get(token, user.id) is None:
        await message.reply_text(messages.REMINDER_PROPOSAL_EXPIRED)
        return True
    proposal = services.reminders.repropose(user, token, message.text or "")
    if proposal is None:
        await message.reply_text(messages.REMINDER_EDIT_UNPARSEABLE)
        return True
    await _show_proposal(message, proposal)
    return True


async def _apply_proposal_action(
    query: CallbackQuery, services: TelegramServices, user: User, action: str, token: str
) -> None:
    reminders = services.reminders
    assert reminders is not None
    if action == "ok":
        proposal = reminders.pending.get(token, user.id)
        reminder_id = await reminders.confirm(user, token)
        if proposal is None or reminder_id is None:
            await query.edit_message_text(messages.REMINDER_INVALID_LINK)
            return
        await query.edit_message_text(
            messages.REMINDER_SAVED.format(
                when=when_azerbaijani(proposal.due_at_utc, proposal.timezone_name)
            )
        )
    elif action == "e":
        if reminders.pending.get(token, user.id) is None:
            await query.edit_message_text(messages.REMINDER_INVALID_LINK)
            return
        services.reminder_edit_state.request(user.telegram_user_id, token)
        await query.edit_message_text(messages.REMINDER_EDIT_PROMPT)
    elif action == "n":
        discarded = reminders.discard(user, token)
        await query.edit_message_text(
            messages.REMINDER_DISCARDED if discarded else messages.REMINDER_INVALID_LINK
        )
    else:
        await query.edit_message_text(messages.TEMPORARY_ERROR)


async def _active_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> tuple[TelegramServices, User, Message] | None:
    """Resolve the private-chat caller to an active account, answering otherwise."""
    identity = _identity(update)
    message = update.effective_message
    if identity is None or message is None:
        return None
    services = _services(context)
    try:
        user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    except SQLAlchemyError:
        logger.exception("Telegram caller lookup failed")
        await message.reply_text(messages.TEMPORARY_ERROR)
        return None
    if user is None:
        await message.reply_text(messages.UNKNOWN_USER)
        return None
    if user.status != UserStatus.ACTIVE:
        await message.reply_text(_status_message(user))
        return None
    return services, user, message


async def handle_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    resolved = await _active_user(update, context)
    if resolved is None:
        return
    services, user, message = resolved
    if services.reminders is None:
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    try:
        rows: list[Reminder] = await services.reminders.list_active(user)
    except (ApplicationError, SQLAlchemyError):
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    if not rows:
        await message.reply_text(messages.REMINDER_NONE)
        return
    await message.reply_text(
        messages.REMINDER_LIST_HEADER.format(count=len(rows)),
    )
    for index, reminder in enumerate(rows, start=1):
        line = messages.REMINDER_LIST_ITEM.format(
            index=index,
            when=when_azerbaijani(reminder.due_at_utc, reminder.timezone),
            text=reminder.text,
        )
        if reminder.status == ReminderStatus.PAUSED.value:
            line += messages.REMINDER_LIST_PAUSED
        await message.reply_text(
            line,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            messages.REMINDER_DISCARD_BUTTON,
                            callback_data=f"rh:c:{reminder.id}",
                        )
                    ]
                ]
            ),
        )


async def handle_reminder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    actor = update.effective_user
    if query is None or actor is None or not _is_private(update):
        return
    await query.answer()
    services = _services(context)
    parts = (query.data or "").split(":")
    if services.reminders is None or len(parts) != 3 or parts[0] not in {"rp", "rh"}:
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    try:
        user = await services.users.get_by_telegram_id(actor.id)
    except SQLAlchemyError:
        logger.exception("Reminder callback lookup failed")
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    if user is None:
        await query.edit_message_text(messages.UNKNOWN_USER)
        return
    if user.status != UserStatus.ACTIVE:
        await query.edit_message_text(_status_message(user))
        return
    try:
        if parts[0] == "rp":
            await _apply_proposal_action(query, services, user, parts[1], parts[2])
        else:
            cancelled = await services.reminders.cancel(user, int(parts[2]))
            await query.edit_message_text(
                messages.REMINDER_CANCELLED if cancelled else messages.REMINDER_NOT_CANCELLABLE
            )
    except (ApplicationError, SQLAlchemyError, ValueError) as error:
        logger.error("Reminder callback failed | error_type=%s", type(error).__name__)
        await query.edit_message_text(messages.TEMPORARY_ERROR)


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    message = update.effective_message
    if message is None:
        return
    await _reply_long(message, messages.HELP_TEXT, _services(context).telegram_message_limit)


def _delete_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(messages.DATA_DELETE_YES_BUTTON, callback_data="rd:yes"),
                InlineKeyboardButton(messages.DATA_DELETE_NO_BUTTON, callback_data="rd:no"),
            ]
        ]
    )


async def handle_delete_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    identity = _identity(update)
    resolved = await _active_user(update, context)
    if resolved is None or identity is None:
        return
    services, _, message = resolved
    if services.reminders is None:
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    services.delete_confirmation_state.request(identity.telegram_user_id)
    await message.reply_text(messages.DATA_DELETE_CONFIRM, reply_markup=_delete_keyboard())


async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    actor = update.effective_user
    if query is None or actor is None or not _is_private(update):
        return
    await query.answer()
    services = _services(context)
    if services.reminders is None:
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    if not services.delete_confirmation_state.contains(actor.id):
        await query.edit_message_text(messages.DATA_DELETE_NO_REQUEST)
        return
    services.delete_confirmation_state.clear(actor.id)
    if query.data == "rd:no":
        await query.edit_message_text(messages.DATA_DELETE_CANCELLED)
        return
    if query.data != "rd:yes":
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    user = await services.users.get_by_telegram_id(actor.id)
    if user is None or user.status != UserStatus.ACTIVE:
        await query.edit_message_text(messages.UNKNOWN_USER)
        return
    try:
        counts = await services.reminders.delete_personal_data(user)
    except (ApplicationError, SQLAlchemyError):
        logger.exception("Personal data deletion failed")
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    services.reminder_edit_state.clear(actor.id)
    await query.edit_message_text(messages.DATA_DELETED.format(**counts))


async def handle_extraction_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    actor = update.effective_user
    if query is None or actor is None or not _is_private(update):
        return
    await query.answer()
    parts = (query.data or "").split(":")
    services = _services(context)
    if len(parts) != 3 or parts[0] != "xr" or services.extraction_reviews is None:
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    try:
        run_id = UUID(parts[2])
        if parts[1] == "a":
            result = await services.extraction_reviews.approve(actor.id, run_id)
            text = messages.EXTRACTION_APPLIED.format(
                applied=result.applied,
                existing=result.already_exists,
                admin=result.needs_admin,
                failed=result.failed,
            )
        elif parts[1] == "p":
            problems = await services.extraction_reviews.problems(actor.id, run_id)
            lines = [
                f"{item.candidate_type}: {', '.join(item.validation_errors or [item.status])}"
                for item in problems
            ]
            text = messages.EXTRACTION_PROBLEMS + ("\n" + "\n".join(lines) if lines else "")
        elif parts[1] == "r":
            await services.extraction_reviews.reject(actor.id, run_id)
            text = messages.EXTRACTION_REJECTED
        else:
            raise ValueError("Unsupported review action")
        await query.edit_message_text(text)
    except AuthorizationDenied:
        await query.edit_message_text(messages.NO_PERMISSION)
    except DomainConflict:
        await query.edit_message_text(messages.EXTRACTION_REVIEW_UNAVAILABLE)
    except (ApplicationError, ValueError) as error:
        logger.error("Extraction review failed | error_type=%s", type(error).__name__)
        await query.edit_message_text(messages.TEMPORARY_ERROR)
    except SQLAlchemyError:
        logger.exception("Extraction review callback failed")
        await query.edit_message_text(messages.TEMPORARY_ERROR)


async def handle_baza(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    identity = _identity(update)
    message = update.effective_message
    if identity is None or message is None:
        return
    services = _services(context)
    user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if user is None:
        await message.reply_text(messages.UNKNOWN_USER)
        return
    if user.status != UserStatus.ACTIVE:
        await message.reply_text(_status_message(user))
        return
    turn = build_turn_context(message, text=_command_text(message).partition(" ")[2].strip())
    question = turn.text
    if not question:
        await message.reply_text(messages.AI_INVALID_QUESTION)
        return
    if services.internal_evidence is None:
        await message.reply_text(messages.AI_NOT_CONFIGURED)
        return
    if services.rate_guard is not None and not services.rate_guard.allow(user.id):
        await message.reply_text(messages.AI_RATE_LIMITED)
        return
    try:
        await _send_typing(context, message.chat_id)
        images = turn.images
        readings = await _read_turn_images(context, services, images, user_id=user.id)
        result = await services.internal_evidence.answer(
            user, question, force_internal=True, image_readings=readings
        )
        await _reply_long(
            message,
            _with_image_notice(_format_internal(result), images, readings),
            services.telegram_message_limit,
        )
    except AnswerGenerationUnavailable:
        await message.reply_text(messages.AI_UNAVAILABLE)
    except RetrievalUnavailable:
        logger.exception("Baza retrieval projection failed")
        await message.reply_text(messages.QDRANT_UNAVAILABLE)
    except AIServiceError:
        await message.reply_text(messages.AI_SOURCE_VALIDATION_FAILED)
    except SQLAlchemyError:
        logger.exception("Baza database operation failed")
        await message.reply_text(messages.TEMPORARY_ERROR)


async def _handle_web_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, combined: bool
) -> None:
    if not _is_private(update):
        return
    identity = _identity(update)
    message = update.effective_message
    if identity is None or message is None:
        return
    services = _services(context)
    user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if user is None:
        await message.reply_text(messages.UNKNOWN_USER)
        return
    if user.status != UserStatus.ACTIVE:
        await message.reply_text(_status_message(user))
        return
    turn = build_turn_context(message, text=_command_text(message).partition(" ")[2].strip())
    question = turn.text
    if not question:
        await message.reply_text(messages.WEB_INVALID_QUESTION)
        return
    if services.web_evidence is None:
        await message.reply_text(messages.WEB_UNAVAILABLE)
        return
    if services.rate_guard is not None and not services.rate_guard.allow(user.id):
        await message.reply_text(messages.AI_RATE_LIMITED)
        return
    try:
        await _send_typing(context, message.chat_id)
        # /internet has no internal evidence to combine with, so its picture is not read; the
        # caption router already told the user the file was not stored. OPEN-QUESTIONS Q23.
        images = turn.images if combined else ()
        readings = await _read_turn_images(context, services, images, user_id=user.id)
        result = (
            await services.web_evidence.answer_combined(user, question, image_readings=readings)
            if combined
            else await services.web_evidence.answer_internet(user, question)
        )
        await _reply_long(
            message,
            _with_image_notice(_format_web(result), images, readings),
            services.telegram_message_limit,
        )
    except (WebEvidenceUnavailable, AnswerGenerationUnavailable):
        await message.reply_text(messages.WEB_UNAVAILABLE)
    except RetrievalUnavailable:
        logger.exception("Web command internal projection failed | combined=%s", combined)
        await message.reply_text(messages.COMBINED_INTERNAL_UNAVAILABLE)
    except (AIServiceError, SQLAlchemyError):
        logger.exception("Web command failed | combined=%s", combined)
        await message.reply_text(messages.TEMPORARY_ERROR)
    except Exception:
        logger.exception("Unexpected web command failure | combined=%s", combined)
        await message.reply_text(messages.TEMPORARY_ERROR)


async def handle_internet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_web_command(update, context, combined=False)


async def handle_tamaxtar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_web_command(update, context, combined=True)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    document = message.document
    if document is None:
        return
    if await _route_caption_command(update, context):
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
    await _run_deferred_caption_command(update, context)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.photo:
        return
    if await _route_caption_command(update, context):
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
    await _run_deferred_caption_command(update, context)
    await _confirm_reference_image(update, context)


async def _confirm_reference_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Queue the picture for the product its caption named, reading no model to do it (§23.7).

    This runs after the upload, because the confirmation is about the file this message carried. A
    caption that is not the naming phrase is left alone: an ordinary caption must never become a
    business instruction (invariant 1), and an ambiguous one writes nothing rather than choosing.
    Nothing becomes authoritative here — ``/saxla`` is what turns the claim into a relation.
    """

    message = update.effective_message
    chat = update.effective_chat
    identity = _identity(update)
    services = _services(context)
    if (
        not _is_private(update)
        or message is None
        or chat is None
        or identity is None
        or services.reference_images is None
    ):
        return
    caption = parse_reference_confirmation(message.caption)
    if isinstance(caption, AmbiguousPictureConfirmation):
        # §23.7: more than one name appeared, so nothing is chosen and nothing is written.
        await message.reply_text(messages.REFERENCE_IMAGE_AMBIGUOUS)
        return
    if not isinstance(caption, ProductPictureConfirmation):
        return
    try:
        result = await services.reference_images.record_product_picture(
            telegram_user_id=identity.telegram_user_id,
            telegram_chat_id=chat.id,
            telegram_message_id=message.message_id,
            canonical_code=caption.code,
        )
    except AuthorizationDenied:
        await message.reply_text(messages.NO_PERMISSION)
        return
    except (ApplicationError, SQLAlchemyError, StorageError, OSError, ValueError):
        logger.exception("Reference image confirmation failed | message_id=%s", message.message_id)
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    await message.reply_text(_reference_image_reply(result, caption.code))


def _reference_image_reply(result: ReferenceImageResult, code: str) -> str:
    """Answer a queued confirmation in Azerbaijani, naming what the system did or refused to do."""

    if result.status is ReferenceImageStatus.QUEUED:
        return messages.REFERENCE_IMAGE_QUEUED.format(code=result.display_code or code)
    if result.status is ReferenceImageStatus.ALREADY_LINKED:
        return messages.REFERENCE_IMAGE_ALREADY_SAVED.format(code=result.display_code or code)
    if result.status is ReferenceImageStatus.PRODUCT_NOT_FOUND:
        return messages.REFERENCE_IMAGE_PRODUCT_NOT_FOUND.format(code=code)
    if result.status is ReferenceImageStatus.CONFLICT:
        return messages.REFERENCE_IMAGE_CONFLICT.format(code=result.display_code or code)
    if result.status is ReferenceImageStatus.NO_PICTURE:
        return messages.REFERENCE_IMAGE_NO_PICTURE
    return messages.REFERENCE_IMAGE_NO_SESSION


async def _run_deferred_caption_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the caption command the router held back, now that the file is stored."""
    message = update.effective_message
    if message is None:
        return
    command = _caption_command(message)
    if command is None or command[0] not in INGEST_THEN_RUN_COMMANDS:
        return
    await COMMAND_CALLBACKS[command[0]](update, context)


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


CommandCallback = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]

COMMAND_CALLBACKS: dict[str, CommandCallback] = {
    "start": handle_start,
    "whoami": handle_whoami,
    "users": handle_users,
    "cancel": handle_cancel,
    "saxla": handle_save,
    "hatirlatmalar": handle_reminders,
    "help": handle_help,
    "verilerimi_sil": handle_delete_data,
    "baza": handle_baza,
    "internet": handle_internet,
    "tamaxtar": handle_tamaxtar,
}
