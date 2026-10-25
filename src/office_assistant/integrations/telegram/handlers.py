"""Thin Telegram handlers delegating to application services."""

import asyncio
import logging
import re
import secrets
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field, replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    Update,
    WebAppInfo,
)
from telegram.constants import ChatAction, ChatType
from telegram.error import NetworkError, TelegramError
from telegram.ext import ContextTypes

from office_assistant.ai.service import (
    AIServiceError,
    AnswerGenerationUnavailable,
    GroundedAnswerService,
    InProcessRateGuard,
    RetrievalIndexNotInitialized,
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
from office_assistant.application.inventory import (
    InventoryConflict,
    InventoryLedgerService,
    InventoryValidationError,
    StockDraft,
    parse_natural_stock_request,
    parse_stock_draft,
    parse_stock_request,
)
from office_assistant.application.products import ProductService
from office_assistant.application.reference_images import (
    ReferenceImageDenialReason,
    ReferenceImageResult,
    ReferenceImageSaveDenied,
    ReferenceImageService,
    ReferenceImageStatus,
    ReferenceRevokeResult,
    ReferenceRevokeStatus,
)
from office_assistant.application.reminders import ReminderProposal, ReminderService
from office_assistant.application.sales_import import MAX_XLSX_BYTES, parse_sales_xlsx
from office_assistant.application.sales_ledger import (
    SaleAlreadyExists,
    SaleDraft,
    SalesLedgerService,
    SalesRequest,
    SaleValidationError,
    parse_sale_draft,
    parse_sales_request,
)
from office_assistant.application.user_management import UserManagementService
from office_assistant.auth.policy import is_admin
from office_assistant.business.normalization import normalize_product_code
from office_assistant.business.product_registration import (
    ProductRegistrationDraft,
    parse_product_registration,
)
from office_assistant.business.reference_confirmation import (
    AmbiguousPictureConfirmation,
    ProductPictureConfirmation,
    parse_reference_confirmation,
)
from office_assistant.extraction.review import ExtractionReviewService
from office_assistant.infrastructure.database.models import (
    FileObject,
    FileObjectState,
    ImageAsset,
    ImageAssetOccurrence,
    ImageAssetOrigin,
    Product,
    ProductImageRelation,
    ProductImageRelationConfirmation,
    Reminder,
    ReminderStatus,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.storage.local import LocalFileStorage, StorageError
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.catalog_source_tickets import (
    CATALOG_SOURCE_TICKET_KEY,
    CatalogSourceTicket,
    issue_catalog_ticket,
    resolve_catalog_ticket,
)
from office_assistant.integrations.telegram.formatting import split_telegram_text
from office_assistant.integrations.telegram.reminder_format import when_azerbaijani
from office_assistant.integrations.telegram.state import (
    DeleteConfirmationState,
    PasswordAttemptLimiter,
    PasswordEntryState,
    ReferenceRevokeState,
    ReminderEditState,
)
from office_assistant.integrations.telegram.turn_context import (
    MediaRef,
    build_turn_context,
    needs_an_attached_picture,
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
from office_assistant.processing.catalog_category_verifier import local_category_verifier
from office_assistant.processing.catalog_visual_candidates import (
    MAX_PDF_REFERENCES,
    PdfImageSource,
    PdfPageCandidate,
    match_pdf_image_sources,
    render_pdf_image_candidates,
    semantic_pdf_image_candidates,
)
from office_assistant.processing.image_similarity import VisualCandidate, shortlist
from office_assistant.processing.pdf_visual_row import read_pdf_visual_details
from office_assistant.processing.semantic_visual import SemanticReference, SemanticVisualSearch
from office_assistant.processing.vision import ImageVisionExtractor
from office_assistant.reminders.time_parsing import looks_like_reminder_request
from office_assistant.repositories.product_image_relations import ProductImageRelationRepository
from office_assistant.web_evidence.models import WebAnswer, WebEvidenceUnavailable
from office_assistant.web_evidence.priority_sites import (
    InvalidPrioritySite,
    PrioritySitePermissionDenied,
    PrioritySitesService,
)
from office_assistant.web_evidence.service import WebEvidenceService

SERVICES_KEY = "phase2_services"
MENU_SELECTION_KEY = "office_menu_selection"
SOURCE_BUTTONS_KEY = "office_source_buttons"
SOURCE_BUTTON_TTL_SECONDS = 600
MENU_SELECTION_TTL_SECONDS = 600
SALES_DRAFT_KEY = "office_sales_draft"
STOCK_DRAFT_KEY = "office_stock_draft"
PRODUCT_REGISTRATION_KEY = "office_product_registration_draft"
PRODUCT_REGISTRATION_TTL_SECONDS = 600
SALES_IMPORT_KEY = "office_sales_import"
SALES_IMPORT_TTL_SECONDS = 600
SALES_CONFIRM_TTL_SECONDS = 600
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
    answer_deadline_seconds: float = 75.0
    extraction_reviews: ExtractionReviewService | None = None
    internal_evidence: InternalEvidenceService | None = None
    knowledge: KnowledgeRetrievalService | None = None
    file_delivery: SourceFileDeliveryService | None = None
    web_evidence: WebEvidenceService | None = None
    image_reader: ImageVisionExtractor | None = None
    semantic_visual: SemanticVisualSearch | None = None
    reminders: ReminderService | None = None
    reference_images: ReferenceImageService | None = None
    sales: SalesLedgerService | None = None
    inventory: InventoryLedgerService | None = None
    miniapp_url: str | None = None
    reminder_edit_state: ReminderEditState = field(default_factory=ReminderEditState)
    delete_confirmation_state: DeleteConfirmationState = field(
        default_factory=DeleteConfirmationState
    )
    reference_revokes: ReferenceRevokeState = field(default_factory=ReferenceRevokeState)


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
    username = (
        f"@{user.telegram_username}" if user.telegram_username else messages.USERS_NO_USERNAME
    )
    return messages.USERS_LINE.format(
        telegram_user_id=user.telegram_user_id,
        name=name or messages.USERS_NO_NAME,
        username=username,
        status=user.status,
    )


async def _reply_long(
    message: object,
    text: str,
    limit: int,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    parts = split_telegram_text(text, limit=limit)
    for index, part in enumerate(parts):
        for attempt in range(3):
            try:
                if reply_markup is not None and index == len(parts) - 1:
                    await message.reply_text(  # type: ignore[attr-defined]
                        part, reply_markup=reply_markup
                    )
                else:
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
    logger.info("Image turn reading summary | attached=%d readable=%d", len(images), len(readings))
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


async def _try_catalog_source_delivery(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    services: TelegramServices,
    actor_telegram_user_id: int,
    text: str,
) -> bool:
    """Understand 'həmin PDF-i ver' after a catalog photo result."""

    data = getattr(context, "user_data", None)
    state: object = data.get(CATALOG_SOURCE_TICKET_KEY) if data is not None else None
    if not isinstance(state, CatalogSourceTicket):
        return False
    requested = parse_file_delivery_request(text)
    if requested is False:
        # A later unrelated question must not silently refer to an old photo.
        if data is not None:
            data.pop(CATALOG_SOURCE_TICKET_KEY, None)
        return False
    number = requested if isinstance(requested, int) and not isinstance(requested, bool) else 1
    if requested is None and len(state.sources) > 1:
        await message.reply_text(messages.SOURCE_FILE_AMBIGUOUS)
        return True
    source = resolve_catalog_ticket(state, actor_telegram_user_id, number)
    if source is None:
        await message.reply_text(messages.MENU_SOURCE_BUTTON_EXPIRED)
        return True
    if services.file_delivery is None:
        await message.reply_text(messages.SOURCE_FILE_UNAVAILABLE)
        return True
    try:
        original = await services.file_delivery.resolve_catalog_original(
            actor_telegram_user_id,
            file_id=source.file_id,
            occurrence_id=source.occurrence_id,
        )
        with original.path.open("rb") as stream:
            await message.reply_document(
                document=stream,
                filename=original.filename,
                read_timeout=60,
                write_timeout=60,
            )
    except (
        AuthorizationDenied,
        FileDeliveryError,
        StorageError,
        SQLAlchemyError,
        OSError,
        TelegramError,
    ):
        await message.reply_text(messages.SOURCE_FILE_UNAVAILABLE)
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


def _menu_keyboard(miniapp_url: str | None = None) -> InlineKeyboardMarkup:
    """Native bot stays complete; optional Web App requires its own HTTPS origin."""

    rows = [
        [
            InlineKeyboardButton(messages.MENU_CHAT_BUTTON, callback_data="om:ask"),
            InlineKeyboardButton(messages.MENU_BAZA_BUTTON, callback_data="om:baza"),
        ],
        [
            InlineKeyboardButton(messages.MENU_PRODUCT_BUTTON, callback_data="om:product"),
            InlineKeyboardButton(messages.MENU_STOCK_BUTTON, callback_data="om:stock"),
        ],
        [
            InlineKeyboardButton(messages.MENU_UPLOAD_BUTTON, callback_data="om:upload"),
            InlineKeyboardButton(messages.MENU_SALES_BUTTON, callback_data="om:sales"),
        ],
        [
            InlineKeyboardButton(messages.MENU_INTERNET_BUTTON, callback_data="om:internet"),
            InlineKeyboardButton(messages.MENU_HELP_BUTTON, callback_data="om:help"),
        ],
    ]
    if miniapp_url is not None and miniapp_url.startswith("https://"):
        rows.insert(
            0,
            [InlineKeyboardButton("✨ Mini App-ı aç", web_app=WebAppInfo(url=miniapp_url))],
        )
    return InlineKeyboardMarkup(rows)


def _answer_keyboard(
    source_token: str | None = None,
    source_numbers: tuple[int, ...] = (),
) -> InlineKeyboardMarkup:
    """Read-only original-source actions plus non-destructive navigation."""

    rows: list[list[InlineKeyboardButton]] = []
    if source_token is not None and source_numbers:
        rows.append(
            [
                InlineKeyboardButton(
                    messages.MENU_SOURCE_FILE_BUTTON.format(number=number),
                    callback_data=f"src:{source_token}:{number}",
                )
                for number in source_numbers
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(messages.MENU_NAV_ASK, callback_data="nav:ask"),
                InlineKeyboardButton(messages.MENU_NAV_BAZA, callback_data="nav:baza"),
            ],
            [InlineKeyboardButton(messages.MENU_NAV_HOME, callback_data="nav:home")],
        ]
    )
    return InlineKeyboardMarkup(rows)


def _answer_markup(
    context: ContextTypes.DEFAULT_TYPE,
    services: TelegramServices,
    answer: InternalAnswer,
) -> InlineKeyboardMarkup:
    """Bind source buttons to the latest grounded answer, never to a stale index."""

    data = getattr(context, "user_data", None)
    if data is None:
        return _answer_keyboard()
    data.pop(SOURCE_BUTTONS_KEY, None)
    if services.file_delivery is None:
        return _answer_keyboard()
    sources = tuple(
        sorted(
            {
                (item.number, item.file_id)
                for item in answer.citations
                if item.file_id is not None and 1 <= item.number <= 99
            }
        )[:3]
    )
    if not sources:
        return _answer_keyboard()
    token = secrets.token_urlsafe(8)
    data[SOURCE_BUTTONS_KEY] = (
        token,
        monotonic() + SOURCE_BUTTON_TTL_SECONDS,
        sources,
    )
    return _answer_keyboard(token, tuple(number for number, _ in sources))


def _catalog_source_keyboard(ticket: CatalogSourceTicket | None) -> InlineKeyboardMarkup:
    """Offer source-file download without changing product confirmation semantics."""

    if ticket is None:
        return _answer_keyboard()
    rows = [
        InlineKeyboardButton(
            messages.MENU_SOURCE_FILE_BUTTON.format(number=item.number),
            callback_data=f"cat:{ticket.token}:{item.number}",
        )
        for item in ticket.sources
    ]
    return InlineKeyboardMarkup([rows, *[list(row) for row in _answer_keyboard().inline_keyboard]])


def _help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(messages.MENU_EXAMPLES_BUTTON, callback_data="om:examples")],
            [InlineKeyboardButton(messages.MENU_NAV_HOME, callback_data="om:home")],
        ]
    )


def _examples_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(messages.MENU_CHAT_BUTTON, callback_data="om:ask"),
                InlineKeyboardButton(messages.MENU_BAZA_BUTTON, callback_data="om:baza"),
            ],
            [InlineKeyboardButton(messages.MENU_NAV_HOME, callback_data="om:home")],
        ]
    )


def _upload_keyboard() -> InlineKeyboardMarkup:
    """A write requires a distinct explicit tap; upload alone never commits."""

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(messages.MENU_SAVE_BUTTON, callback_data="om:save"),
                InlineKeyboardButton(messages.MENU_CANCEL_BUTTON, callback_data="om:cancel"),
            ],
            [InlineKeyboardButton("🏠 Əsas menyu", callback_data="om:home")],
        ]
    )


def _is_save_intent(text: str) -> bool:
    """Only unambiguous exact requests trigger the *confirmation prompt*, never a write."""

    normalized = " ".join(text.casefold().rstrip("!?. ").split())
    return normalized in {
        "bazaya yaz",
        "bunu bazaya yaz",
        "yadda saxla",
        "bunu yadda saxla",
        "bunları yadda saxla",
        "bu sənədi saxla",
        "faylları saxla",
    }


def _note_payload(text: str) -> str | None:
    """Extract only explicitly labelled text notes; do not infer a write from chat."""

    match = re.match(r"^\s*(?:bazaya yaz|yadda saxla)\s*[:：-]\s*(.+)$", text, re.I | re.S)
    if match is None:
        return None
    payload = match.group(1).strip()
    return payload if 0 < len(payload) <= 4000 else None


def _pending_document_instruction(text: str) -> str | None:
    """Only an explicitly labelled instruction can modify a staged document.

    Ordinary conversation must not silently become ingestion instructions, even
    when the user has left a document awaiting confirmation.
    """

    match = re.fullmatch(
        r"\s*(?:fayla qeyd|sənədə qeyd|fayl üçün təlimat)\s*[:：]\s*(.*?)\s*",
        text,
        re.I | re.S,
    )
    if match is None:
        return None
    instruction = match.group(1).strip()
    return instruction if 0 < len(instruction) <= 4000 else None


async def _try_text_note_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    services: TelegramServices,
    user: User,
    text: str,
) -> bool:
    """Stage an explicit text note as TXT so the existing parser and worker can index it."""

    message = update.effective_message
    identity = _identity(update)
    if message is None or identity is None:
        return False
    payload = _note_payload(text)
    if payload is None and _is_save_intent(text):
        replied = message.reply_to_message
        if (
            replied is not None
            and replied.from_user is not None
            and replied.from_user.id == identity.telegram_user_id
            and replied.text
            and len(replied.text) <= 4000
        ):
            payload = replied.text
        else:
            await message.reply_text(
                "Nəyi bazaya yazım? Mətni «Bazaya yaz: KA45 50 AZN-dir» "
                "şəklində göndərin və ya öz mesajınıza cavab verib «Bunu bazaya yaz» yazın."
            )
            return True
    if payload is None:
        return False
    _discard_menu_selection(context)
    if services.storage is None or services.ingestion is None:
        await message.reply_text(messages.STORAGE_ERROR)
        return True
    staging_path, staging_key = services.storage.create_staging_path(".txt")
    try:
        staging_path.write_text(payload, encoding="utf-8")
        await services.ingestion.add_file(
            user,
            UploadMetadata(
                original_filename=f"telegram_qeyd_{message.message_id}.txt",
                content_type="text/plain",
                staging_path=staging_path,
                staging_key=staging_key,
                telegram_chat_id=message.chat_id,
                telegram_message_id=message.message_id,
                telegram_update_id=update.update_id,
                caption=None,
            ),
        )
    except (ApplicationError, SQLAlchemyError, StorageError, OSError) as error:
        logger.warning("Text note staging failed | error_type=%s", type(error).__name__)
        services.storage.delete(staging_key)
        await message.reply_text(messages.STORAGE_ERROR)
        return True
    await message.reply_text(
        "📝 MƏLUMAT TƏKLİFİ\n\n"
        "Qeyd müvəqqəti sessiyaya əlavə olundu.\n"
        "Qeydlər yalnız təsdiqdən sonra emal edilir. "
        "Qiymət, stok və satış kimi rəsmi qeydlər ayrıca yoxlanacaq.",
        reply_markup=_upload_keyboard(),
    )
    return True


def _sales_confirmation_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Təsdiqlə", callback_data=f"sl:ok:{token}"),
                InlineKeyboardButton("Ləğv et", callback_data=f"sl:no:{token}"),
            ]
        ]
    )


def _format_sales_draft(draft: SaleDraft) -> str:
    return (
        "📋 SATIŞ FAKTURASI — TƏSDİQ\n\n"
        f"Müştəri: {draft.customer_name}\n"
        f"Faktura: {draft.invoice_number}\n"
        f"Tarix: {draft.sold_on.isoformat()}\n"
        f"Məbləğ: {draft.amount:.2f} {draft.currency}\n\n"
        "Yalnız həqiqətən tamamlanmış satışı təsdiqləyin. "
        "Təklif və alınan ödəniş satış deyil."
    )


def _stock_confirmation_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Təsdiqlə", callback_data=f"st:ok:{token}"),
                InlineKeyboardButton("Ləğv et", callback_data=f"st:no:{token}"),
            ]
        ]
    )


async def _try_stock_turn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    services: TelegramServices,
    actor: User,
    text: str,
) -> bool:
    """Keep stock inquiries and approved manual inventory mutations out of model RAG."""

    if update.effective_message is None:
        return False
    try:
        draft = parse_stock_draft(text)
        lookup = None if draft is not None else parse_stock_request(text)
        if lookup is None and draft is None and services.inventory is not None:
            lookup = parse_natural_stock_request(text)
    except InventoryValidationError as error:
        await update.effective_message.reply_text(str(error))
        return True
    if draft is None and lookup is None:
        return False
    _discard_menu_selection(context)
    if services.inventory is None:
        await update.effective_message.reply_text("Anbar uçotu xidməti hələ aktiv deyil.")
        return True
    if draft is not None:
        if actor.role != UserRole.ADMIN:
            await update.effective_message.reply_text(
                "Stok giriş-çıxışını yalnız administrator təsdiqləyə bilər."
            )
            return True
        data = getattr(context, "user_data", None)
        if data is None:
            await update.effective_message.reply_text(messages.TEMPORARY_ERROR)
            return True
        token = secrets.token_urlsafe(12)
        data[STOCK_DRAFT_KEY] = (token, monotonic() + 600, draft)
        descriptions = {"OPENING": "Açılış qalığı", "RECEIPT": "Giriş", "ISSUE": "Çıxış"}
        await update.effective_message.reply_text(
            "📦 ANBAR ƏMƏLİYYATI — TƏSDİQ\n\n"
            f"Məhsul: {draft.product_code}\n"
            f"Anbar: {draft.warehouse}\n"
            f"Əməliyyat: {descriptions[draft.kind]}\n"
            f"Miqdar: {draft.quantity:.3f}\n"
            f"Sənəd: {draft.reference}\n\n"
            "Yalnız real, sənədləşdirilmiş hərəkəti təsdiqləyin.",
            reply_markup=_stock_confirmation_keyboard(token),
        )
        return True
    assert lookup is not None
    try:
        snapshot = await services.inventory.snapshot(actor, lookup)
    except InventoryValidationError as error:
        await update.effective_message.reply_text(str(error))
        return True
    except AuthorizationDenied:
        await update.effective_message.reply_text(messages.NO_PERMISSION)
        return True
    if snapshot.total_reason == "UNVERIFIED_UNIT":
        breakdown = "\n".join(
            f"• {item.warehouse}: {item.quantity:.3f}" for item in snapshot.locations
        )
        await update.effective_message.reply_text(
            f"📦 ANBAR QALIĞI\n\nMəhsul: {snapshot.product_code}\n"
            f"{breakdown}\n\n{messages.STOCK_UNIT_UNKNOWN}\n" + messages.STOCK_SUM_INCOMPARABLE
        )
        return True
    if snapshot.total is None:
        await update.effective_message.reply_text(
            f"📦 ANBAR QALIĞI\n\nMəhsul: {snapshot.product_code}\n"
            "Təsdiqlənmiş açılış qalığı yoxdur; stok MƏLUM DEYİL. "
            "Kataloqda olması anbarda olduğunu sübut etmir."
        )
        return True
    breakdown = "\n".join(f"• {item.warehouse}: {item.quantity:.3f}" for item in snapshot.locations)
    unit_label = (
        f"Ölçü vahidi: {snapshot.unit_code}"
        if snapshot.unit_code is not None
        else messages.STOCK_UNIT_UNKNOWN
    )
    await update.effective_message.reply_text(
        f"📦 ANBAR QALIĞI\n\nMəhsul: {snapshot.product_code}\n"
        f"{breakdown}\n\n{unit_label}\nTəsdiqlənmiş anbarlar üzrə cəm: {snapshot.total:.3f}\n"
        + messages.STOCK_PARTIAL_SCOPE
    )
    return True


async def handle_stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update) or update.effective_message is None:
        return
    identity = _identity(update)
    if identity is None:
        return
    services = _services(context)
    user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        await update.effective_message.reply_text(messages.NO_PERMISSION)
        return
    raw = update.effective_message.text or ""
    code = raw.partition(" ")[2].strip()
    if not code:
        await update.effective_message.reply_text(
            "Məhsul kodunu yazın: /stok KA45\n"
            "Yeni anbar qalığı: Anbar əməliyyatı: KA45 | Əsas | AÇILIŞ | 10 | SAY-001"
        )
        return
    await _try_stock_turn(update, context, services, user, "Stok: " + code)


async def handle_stock_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """One verified, expiring admin approval; callbacks cannot directly set quantities."""

    query = update.callback_query
    identity = _identity(update)
    if not _is_private(update) or query is None or identity is None:
        return
    await query.answer()
    data = getattr(context, "user_data", None)
    if data is None:
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    raw: object = data.get(STOCK_DRAFT_KEY)
    selected = cast(tuple[object, ...], raw) if isinstance(raw, tuple) else ()
    parts = (query.data or "").split(":")
    if (
        len(selected) != 3
        or len(parts) != 3
        or parts[0] != "st"
        or parts[1] not in {"ok", "no"}
        or selected[0] != parts[2]
        or not isinstance(selected[1], (int, float))
        or monotonic() > selected[1]
        or not isinstance(selected[2], StockDraft)
    ):
        data.pop(STOCK_DRAFT_KEY, None)
        await query.edit_message_text("Bu stok təsdiqi artıq etibarlı deyil.")
        return
    data.pop(STOCK_DRAFT_KEY, None)
    if parts[1] == "no":
        await query.edit_message_text("Anbar əməliyyatı ləğv edildi; heç nə yazılmadı.")
        return
    services = _services(context)
    if services.inventory is None:
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if user is None or user.status != UserStatus.ACTIVE or user.role != UserRole.ADMIN:
        await query.edit_message_text(messages.NO_PERMISSION)
        return
    try:
        entry = await services.inventory.record(user, selected[2])
    except InventoryConflict as error:
        await query.edit_message_text(str(error))
        return
    except InventoryValidationError as error:
        await query.edit_message_text(str(error))
        return
    except AuthorizationDenied:
        await query.edit_message_text(messages.NO_PERMISSION)
        return
    except SQLAlchemyError:
        logger.exception("Stock ledger write failed")
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    await query.edit_message_text(
        f"✅ ANBAR QEYDİ TƏSDİQLƏNDİ\n\n"
        f"Məhsul: {entry.product_code}\nAnbar: {entry.warehouse}\n"
        f"Sənəd: {entry.reference}\nYeni qalıq: {entry.balance_after:.3f}\n"
        "Əməliyyat audit jurnalına daxil edildi."
    )


async def _try_sales_turn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    services: TelegramServices,
    user: User,
    text: str,
) -> bool:
    """Handle explicit sale creation and unambiguous yearly reports before general RAG."""

    if services.sales is None or update.effective_message is None:
        return False
    try:
        draft = parse_sale_draft(text)
    except SaleValidationError as error:
        await update.effective_message.reply_text(str(error))
        return True
    if draft is not None:
        _discard_menu_selection(context)
        if user.role != UserRole.ADMIN:
            await update.effective_message.reply_text(
                "Satış fakturasını yalnız administrator təsdiqləyə bilər. "
                "Fakturanı administratora təqdim edin."
            )
            return True
        data = getattr(context, "user_data", None)
        if data is None:
            await update.effective_message.reply_text(messages.TEMPORARY_ERROR)
            return True
        token = secrets.token_urlsafe(12)
        data[SALES_DRAFT_KEY] = (token, monotonic() + SALES_CONFIRM_TTL_SECONDS, draft)
        await update.effective_message.reply_text(
            _format_sales_draft(draft),
            reply_markup=_sales_confirmation_keyboard(token),
        )
        return True
    request = parse_sales_request(text)
    if request is None:
        return False
    _discard_menu_selection(context)
    await _render_sales_report(update.effective_message, services, user, request)
    return True


async def _render_sales_report(
    message: Message, services: TelegramServices, actor: User, request: SalesRequest
) -> None:
    if services.sales is None:
        await message.reply_text("Satış hesabatı xidməti konfiqurasiya edilməyib.")
        return
    try:
        totals = await services.sales.totals(actor, request)
    except (AuthorizationDenied, SaleValidationError):
        await message.reply_text(messages.NO_PERMISSION)
        return
    except SQLAlchemyError:
        logger.exception("Sales report database failure")
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    if not totals:
        await message.reply_text(
            f"📊 SATIŞ HESABATI\n\nMüştəri: {request.customer_name}\n"
            f"İl: {request.year}\n\n"
            "Bu müştəri üçün həmin ildə təsdiqlənmiş satış fakturası tapılmadı. "
            "Bu, real satışın sıfır olduğunu sübut etmir: fakturalar hələ "
            "sistemə daxil edilməmiş ola bilər."
        )
        return
    rows = "\n".join(
        f"• {item.amount:,.2f} {item.currency} — {item.invoice_count} faktura" for item in totals
    )
    await message.reply_text(
        f"📊 SATIŞ HESABATI\n\nMüştəri: {request.customer_name}\n"
        f"İl: {request.year}\n\n{rows}\n\n"
        "Yalnız sistemə daxil edilmiş, təsdiqlənmiş fakturalar hesablanıb. "
        "Valyutalar birləşdirilmir, təkliflər və ödənişlər daxil deyil."
    )


async def _handle_sales_report(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, *, from_menu: bool
) -> None:
    message = update.effective_message
    identity = _identity(update)
    if message is None or identity is None or not _is_private(update):
        return
    services = _services(context)
    user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        await message.reply_text(messages.NO_PERMISSION)
        return
    request = parse_sales_request(text)
    if request is None:
        await message.reply_text(
            "Şirkət və ili yazın. Məsələn: A Şirkəti | 2026\n"
            "və ya: Bu il A şirkətinə nə qədər satmışıq?"
        )
        return
    await _render_sales_report(message, services, user, request)


async def handle_sales_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """One authenticated, expiring and non-replayable sale approval."""

    query = update.callback_query
    identity = _identity(update)
    if not _is_private(update) or query is None or identity is None:
        return
    await query.answer()
    data = getattr(context, "user_data", None)
    if data is None:
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    selected: object = data.get(SALES_DRAFT_KEY)
    selected_items: tuple[object, ...] = (
        cast(tuple[object, ...], selected) if isinstance(selected, tuple) else ()
    )
    parts = (query.data or "").split(":")
    if (
        len(selected_items) != 3
        or len(parts) != 3
        or parts[0] != "sl"
        or parts[1] not in {"ok", "no"}
        or selected_items[0] != parts[2]
        or not isinstance(selected_items[1], (int, float))
        or monotonic() > selected_items[1]
        or not isinstance(selected_items[2], SaleDraft)
    ):
        data.pop(SALES_DRAFT_KEY, None)
        await query.edit_message_text(
            "Bu təsdiq artıq etibarlı deyil. Əməliyyatı yenidən başladın."
        )
        return
    data.pop(SALES_DRAFT_KEY, None)
    if parts[1] == "no":
        await query.edit_message_text("Satış qeydi ləğv olundu. Bazaya heç nə yazılmadı.")
        return
    services = _services(context)
    if services.sales is None:
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if user is None or user.status != UserStatus.ACTIVE or user.role != UserRole.ADMIN:
        await query.edit_message_text(messages.NO_PERMISSION)
        return
    try:
        entry = await services.sales.record(user, selected_items[2])
    except SaleAlreadyExists as error:
        await query.edit_message_text(str(error))
        return
    except AuthorizationDenied:
        await query.edit_message_text(messages.NO_PERMISSION)
        return
    except SQLAlchemyError:
        logger.exception("Sales ledger write failed")
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    await query.edit_message_text(
        f"✅ SATIŞ QEYD EDİLDİ\n\nFaktura: {entry.invoice_number}\n"
        f"Müştəri: {entry.customer_name}\n"
        f"Məbləğ: {entry.amount:.2f} {entry.currency}\n"
        "Qeyd audit jurnalına daxil edildi."
    )


def _discard_menu_selection(context: ContextTypes.DEFAULT_TYPE) -> None:
    """A scope choice is temporary; other actions must not leave it armed."""

    data = getattr(context, "user_data", None)
    if data is not None:
        data.pop(MENU_SELECTION_KEY, None)


async def handle_menu_interruption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear one-shot search intent before processing commands, photos or documents."""

    if _is_private(update):
        _discard_menu_selection(context)


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Offer ordinary-language guidance after verifying a private active account."""

    if not _is_private(update):
        return
    identity = _identity(update)
    message = update.effective_message
    if identity is None or message is None:
        return
    _discard_menu_selection(context)
    services = _services(context)
    try:
        user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    except SQLAlchemyError:
        logger.exception("Telegram menu database lookup failed")
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    if user is None:
        await message.reply_text(messages.UNKNOWN_USER)
    elif user.status != UserStatus.ACTIVE:
        await message.reply_text(_status_message(user))
    else:
        await message.reply_text(
            messages.MENU_TITLE, reply_markup=_menu_keyboard(services.miniapp_url)
        )


async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """One menu tap selects the route for exactly one subsequent ordinary text message."""

    if not _is_private(update):
        return
    identity = _identity(update)
    query = update.callback_query
    if identity is None or query is None or not query.data:
        return
    await query.answer()
    action = query.data
    if action not in {
        "om:ask",
        "om:product",
        "om:sales",
        "om:stock",
        "om:home",
        "om:baza",
        "om:internet",
        "om:tamaxtar",
        "om:upload",
        "om:help",
        "om:examples",
        "om:save",
        "om:cancel",
    }:
        return
    # A later tap replaces the earlier scope, including help, upload and access denial.
    # Otherwise a prior internet selection may disclose an unrelated later plain message.
    _discard_menu_selection(context)
    services = _services(context)
    try:
        user = await services.users.get_by_telegram_id(identity.telegram_user_id)
        if user is None:
            await query.edit_message_text(messages.UNKNOWN_USER)
            return
        if user.status != UserStatus.ACTIVE:
            await query.edit_message_text(_status_message(user))
            return
        if action == "om:home":
            await query.edit_message_text(
                messages.MENU_TITLE, reply_markup=_menu_keyboard(services.miniapp_url)
            )
            return
        if action == "om:help":
            await query.edit_message_text(messages.MENU_QUICK_HELP, reply_markup=_help_keyboard())
            return
        if action == "om:examples":
            await query.edit_message_text(messages.MENU_EXAMPLES, reply_markup=_examples_keyboard())
            return
        if action == "om:save":
            await handle_save(update, context)
            return
        if action == "om:cancel":
            await handle_cancel(update, context)
            return
        if action == "om:upload":
            await query.edit_message_text(
                messages.MENU_UPLOAD_GUIDE, reply_markup=_upload_keyboard()
            )
            return
        if action == "om:ask":
            await query.edit_message_text(messages.MENU_CHAT_PROMPT)
            return
        # A pending document may remain staged while the user asks questions,
        # checks a product, or runs a sale report. Buttons must not lock the chat.
        data = getattr(context, "user_data", None)
        if data is None:
            await query.edit_message_text(messages.MENU_TEMPORARY_UNAVAILABLE)
            return
        choice = action.partition(":")[2]
        mode = "baza" if choice == "product" else choice
        data[MENU_SELECTION_KEY] = (mode, monotonic() + MENU_SELECTION_TTL_SECONDS)
        prompt = {
            "baza": messages.MENU_ASK_INTERNAL,
            "product": messages.MENU_PRODUCT_PROMPT,
            "sales": messages.MENU_SALES_PROMPT,
            "stock": messages.MENU_STOCK_PROMPT,
            "internet": messages.MENU_ASK_INTERNET,
            "tamaxtar": messages.MENU_ASK_COMBINED,
        }[choice]
        await query.edit_message_text(prompt)
    except SQLAlchemyError:
        logger.exception("Telegram menu callback database lookup failed")
        await query.edit_message_text(messages.TEMPORARY_ERROR)


async def handle_catalog_source_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download the original PDF for a recent, actor-bound visual source match."""

    if not _is_private(update):
        return
    query = update.callback_query
    identity = _identity(update)
    if query is None or identity is None:
        return
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "cat" or not parts[2].isdigit():
        return
    data = getattr(context, "user_data", None)
    state: object = data.get(CATALOG_SOURCE_TICKET_KEY) if data is not None else None
    source = resolve_catalog_ticket(state, identity.telegram_user_id, int(parts[2]), token=parts[1])
    if source is None:
        await context.bot.send_message(
            chat_id=identity.telegram_user_id,
            text=messages.MENU_SOURCE_BUTTON_EXPIRED,
        )
        return
    service = _services(context).file_delivery
    if service is None:
        await context.bot.send_message(
            chat_id=identity.telegram_user_id,
            text=messages.MENU_SOURCE_NO_FILE,
        )
        return
    try:
        original = await service.resolve_catalog_original(
            identity.telegram_user_id,
            file_id=source.file_id,
            occurrence_id=source.occurrence_id,
        )
        with original.path.open("rb") as stream:
            await context.bot.send_document(
                chat_id=identity.telegram_user_id,
                document=stream,
                filename=original.filename,
                read_timeout=60,
                write_timeout=60,
            )
    except (
        AuthorizationDenied,
        FileDeliveryError,
        StorageError,
        SQLAlchemyError,
        OSError,
        TelegramError,
    ):
        await context.bot.send_message(
            chat_id=identity.telegram_user_id,
            text=messages.MENU_SOURCE_NO_FILE,
        )


async def handle_source_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deliver only a still-authorized source for this user's latest grounded answer."""

    if not _is_private(update):
        return
    query = update.callback_query
    identity = _identity(update)
    if query is None or identity is None:
        return
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "src" or not parts[2].isdigit():
        return
    citation_index = int(parts[2])
    data = getattr(context, "user_data", None)
    state: object = data.get(SOURCE_BUTTONS_KEY) if data is not None else None
    selected = cast(tuple[object, ...], state) if isinstance(state, tuple) else ()
    if (
        len(selected) != 3
        or selected[0] != parts[1]
        or not isinstance(selected[1], (int, float))
        or monotonic() > selected[1]
        or not isinstance(selected[2], tuple)
    ):
        await context.bot.send_message(
            chat_id=identity.telegram_user_id,
            text=messages.MENU_SOURCE_BUTTON_EXPIRED,
        )
        return
    authorized_file_id: int | None = None
    for entry in cast(tuple[object, ...], selected[2]):
        if not isinstance(entry, tuple):
            continue
        pair = cast(tuple[object, ...], entry)
        if len(pair) == 2 and pair[0] == citation_index and isinstance(pair[1], int):
            authorized_file_id = pair[1]
            break
    if authorized_file_id is None:
        await context.bot.send_message(
            chat_id=identity.telegram_user_id,
            text=messages.MENU_SOURCE_BUTTON_EXPIRED,
        )
        return
    service = _services(context).file_delivery
    if service is None:
        await context.bot.send_message(
            chat_id=identity.telegram_user_id,
            text=messages.MENU_SOURCE_NO_FILE,
        )
        return
    try:
        file = await service.resolve_recent(identity.telegram_user_id, citation_index)
        if file.file_id != authorized_file_id:
            await context.bot.send_message(
                chat_id=identity.telegram_user_id,
                text=messages.MENU_SOURCE_BUTTON_EXPIRED,
            )
            return
        with file.path.open("rb") as stream:
            await context.bot.send_document(
                chat_id=identity.telegram_user_id,
                document=stream,
                filename=file.filename,
                read_timeout=60,
                write_timeout=60,
            )
    except (AuthorizationDenied, FileDeliveryError, StorageError, SQLAlchemyError, OSError):
        await context.bot.send_message(
            chat_id=identity.telegram_user_id,
            text=messages.MENU_SOURCE_NO_FILE,
        )


async def handle_answer_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep the answer and citations visible when navigating back to a new turn."""

    if not _is_private(update):
        return
    query = update.callback_query
    identity = _identity(update)
    if query is None or identity is None:
        return
    await query.answer()
    action = query.data or ""
    if action not in {"nav:ask", "nav:baza", "nav:home"}:
        return
    services = _services(context)
    try:
        actor = await services.users.get_by_telegram_id(identity.telegram_user_id)
        if actor is None or actor.status != UserStatus.ACTIVE:
            await context.bot.send_message(
                chat_id=identity.telegram_user_id, text=messages.NO_PERMISSION
            )
            return
        _discard_menu_selection(context)
        if action == "nav:baza":
            data = getattr(context, "user_data", None)
            if data is None:
                await context.bot.send_message(
                    chat_id=identity.telegram_user_id,
                    text=messages.MENU_TEMPORARY_UNAVAILABLE,
                )
                return
            data[MENU_SELECTION_KEY] = ("baza", monotonic() + MENU_SELECTION_TTL_SECONDS)
            content = messages.MENU_ASK_INTERNAL
            keyboard = _examples_keyboard()
        elif action == "nav:ask":
            content = messages.MENU_CHAT_PROMPT
            keyboard = _examples_keyboard()
        else:
            content = messages.MENU_TITLE
            keyboard = _menu_keyboard(services.miniapp_url)
        await context.bot.send_message(
            chat_id=identity.telegram_user_id, text=content, reply_markup=keyboard
        )
    except SQLAlchemyError:
        logger.exception("Answer navigation database lookup failed")
        await context.bot.send_message(
            chat_id=identity.telegram_user_id, text=messages.TEMPORARY_ERROR
        )


async def _try_menu_question(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Consume one text turn with the selected explicit scope, then clear the selection."""

    data = getattr(context, "user_data", None)
    if data is None:
        return False
    selected: object = data.pop(MENU_SELECTION_KEY, None)
    if not isinstance(selected, tuple):
        return False
    selection = cast(tuple[object, ...], selected)
    if len(selection) != 2:
        return False
    mode, expires_at = selection
    if (
        not isinstance(mode, str)
        or mode not in {"baza", "internet", "tamaxtar", "sales", "stock"}
        or not isinstance(expires_at, (int, float))
    ):
        return False
    if monotonic() > expires_at:
        if update.effective_message is not None:
            await update.effective_message.reply_text(messages.MENU_SESSION_EXPIRED)
        return True
    if mode == "baza":
        await handle_baza(update, context, question_override=text)
    elif mode == "sales":
        await _handle_sales_report(update, context, text, from_menu=True)
    elif mode == "stock":
        message = update.effective_message
        identity = _identity(update)
        if message is not None and identity is not None:
            services = _services(context)
            user = await services.users.get_by_telegram_id(identity.telegram_user_id)
            if user is None or user.status != UserStatus.ACTIVE:
                await message.reply_text(messages.NO_PERMISSION)
            else:
                await _try_stock_turn(update, context, services, user, "Stok: " + text.strip())
    else:
        await _handle_web_command(
            update, context, combined=(mode == "tamaxtar"), question_override=text
        )
    return True


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
    if user.status == UserStatus.ACTIVE:
        await message.reply_text(
            messages.WELCOME, reply_markup=_menu_keyboard(services.miniapp_url)
        )
    else:
        await message.reply_text(_status_message(user))


async def handle_password_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    identity = _identity(update)
    message = update.effective_message
    if identity is None or message is None or not message.text:
        return
    services = _services(context)
    data = getattr(context, "user_data", None)
    if data is not None:
        data.pop(SOURCE_BUTTONS_KEY, None)
    if not services.password_state.contains(identity.telegram_user_id):
        try:
            user = await services.users.get_by_telegram_id(identity.telegram_user_id)
            if user is None:
                await message.reply_text(messages.UNKNOWN_USER)
            elif user.status != UserStatus.ACTIVE:
                await message.reply_text(_status_message(user))
            elif await _try_reminder_edit(message, services, user, identity.telegram_user_id):
                return
            elif (
                services.ingestion is not None
                and _is_save_intent(message.text)
                and await services.ingestion.has_open_session(user)
            ):
                await message.reply_text(
                    messages.MENU_SAVE_CONFIRM, reply_markup=_upload_keyboard()
                )
                return
            elif await _try_stock_turn(update, context, services, user, message.text):
                return
            elif await _try_sales_turn(update, context, services, user, message.text):
                return
            elif await _try_text_note_upload(update, context, services, user, message.text):
                return
            elif (
                services.ingestion is not None
                and _pending_document_instruction(message.text) is not None
                and await services.ingestion.has_open_session(user)
            ):
                _discard_menu_selection(context)
                instruction = _pending_document_instruction(message.text)
                assert instruction is not None
                added = await services.ingestion.add_instruction(
                    user,
                    text_content=instruction,
                    chat_id=update.effective_chat.id if update.effective_chat else 0,
                    message_id=message.message_id,
                    update_id=update.update_id,
                )
                await message.reply_text(
                    messages.INSTRUCTION_ADDED if added else messages.NO_OPEN_SESSION
                )
                return
            elif await _try_catalog_source_delivery(
                message, context, services, identity.telegram_user_id, message.text
            ):
                return
            elif await _try_file_delivery(
                message, services, identity.telegram_user_id, message.text
            ):
                return
            elif await _try_menu_question(update, context, message.text):
                return
            elif await _try_reminder_proposal(message, services, user, message.text):
                return
            elif services.internal_evidence is not None and services.rate_guard is not None:
                if not services.rate_guard.allow(user.id):
                    await message.reply_text(messages.AI_RATE_LIMITED)
                    return
                await _send_typing(context, message.chat_id)
                # A picture the user replies to is a first-class parameter of the question.
                async with asyncio.timeout(services.answer_deadline_seconds):
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
                    reply_markup=_answer_markup(context, services, result),
                )
            elif services.ai is not None and services.rate_guard is not None:
                if not services.rate_guard.allow(user.id):
                    await message.reply_text(messages.AI_RATE_LIMITED)
                    return
                await _send_typing(context, message.chat_id)
                async with asyncio.timeout(services.answer_deadline_seconds):
                    result = await services.ai.answer_general(user.id, message.text)
                await _reply_long(message, result.answer, services.telegram_message_limit)
            elif services.ai is None:
                await message.reply_text(messages.AI_NOT_CONFIGURED)
            else:
                await message.reply_text(messages.NO_OPEN_SESSION)
        except TimeoutError:
            logger.warning("Telegram text answer exceeded bounded deadline")
            await message.reply_text(messages.AI_TIMEOUT)
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
    # A callback operates on the BOT'S original menu message, not on /saxla input.
    # Never turn arbitrary bot-authored menu guidance into an ingestion instruction.
    trailing_text = (
        _command_text(message).partition(" ")[2].strip() if update.callback_query is None else ""
    )
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


async def handle_baza(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, question_override: str | None = None
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
    turn = build_turn_context(
        message,
        text=(
            question_override
            if question_override is not None
            else _command_text(message).partition(" ")[2].strip()
        ),
    )
    question = turn.text
    if not question:
        await message.reply_text(messages.AI_INVALID_QUESTION)
        return
    if not turn.images and needs_an_attached_picture(question):
        await message.reply_text(messages.IMAGE_CONTEXT_MISSING)
        return
    if services.internal_evidence is None:
        await message.reply_text(messages.AI_NOT_CONFIGURED)
        return
    if services.rate_guard is not None and not services.rate_guard.allow(user.id):
        await message.reply_text(messages.AI_RATE_LIMITED)
        return
    try:
        await _send_typing(context, message.chat_id)
        async with asyncio.timeout(services.answer_deadline_seconds):
            images = turn.images
            readings = await _read_turn_images(context, services, images, user_id=user.id)
            if images and not readings and needs_an_attached_picture(question):
                await message.reply_text(messages.IMAGE_READING_UNAVAILABLE)
                return
            result = await services.internal_evidence.answer(
                user, question, force_internal=True, image_readings=readings
            )
        await _reply_long(
            message,
            _with_image_notice(_format_internal(result), images, readings),
            services.telegram_message_limit,
            reply_markup=_answer_markup(context, services, result),
        )
    except TimeoutError:
        logger.warning("Baza answer exceeded bounded deadline")
        await message.reply_text(messages.AI_TIMEOUT)
    except AnswerGenerationUnavailable:
        await message.reply_text(messages.AI_UNAVAILABLE)
    except RetrievalIndexNotInitialized:
        await message.reply_text(messages.QDRANT_INDEX_NOT_INITIALIZED)
    except RetrievalUnavailable:
        logger.exception("Baza retrieval projection failed")
        await message.reply_text(messages.QDRANT_UNAVAILABLE)
    except AIServiceError:
        await message.reply_text(messages.AI_SOURCE_VALIDATION_FAILED)
    except SQLAlchemyError:
        logger.exception("Baza database operation failed")
        await message.reply_text(messages.TEMPORARY_ERROR)


async def _handle_web_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    combined: bool,
    question_override: str | None = None,
) -> None:
    if not _is_private(update):
        return
    identity = _identity(update)
    message = update.effective_message
    if identity is None or message is None:
        return
    services = _services(context)
    data = getattr(context, "user_data", None)
    if data is not None:
        data.pop(SOURCE_BUTTONS_KEY, None)
    user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if user is None:
        await message.reply_text(messages.UNKNOWN_USER)
        return
    if user.status != UserStatus.ACTIVE:
        await message.reply_text(_status_message(user))
        return
    turn = build_turn_context(
        message,
        text=(
            question_override
            if question_override is not None
            else _command_text(message).partition(" ")[2].strip()
        ),
    )
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
        async with asyncio.timeout(services.answer_deadline_seconds):
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
    except TimeoutError:
        logger.warning("Web answer exceeded bounded deadline | combined=%s", combined)
        await message.reply_text(messages.AI_TIMEOUT)
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


async def handle_priority_sites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the ordered website list; only ACTIVE admins can change it."""

    if not _is_private(update):
        return
    message = update.effective_message
    identity = _identity(update)
    if message is None or identity is None:
        return
    services = _services(context)
    if services.session_factory is None:
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    try:
        actor = await services.users.get_by_telegram_id(identity.telegram_user_id)
        if actor is None or actor.status != UserStatus.ACTIVE:
            await message.reply_text(messages.NO_PERMISSION)
            return
        cmd, _, value = (message.text or "").partition(" ")
        cmd = cmd.partition("@")[0].casefold()
        priority = PrioritySitesService(services.session_factory)
        if cmd == "/saytlar":
            domains = await priority.list_domains()
            if not domains:
                await message.reply_text(
                    "Prioritet sayt siyahısı boşdur. İnternet axtarışı ümumi mənbələrdə aparılır."
                )
            else:
                lines = "\n".join(
                    f"{index}. {domain}" for index, domain in enumerate(domains, start=1)
                )
                await message.reply_text("🌐 PRIORİTET SAYTLAR\n\n" + lines)
            return
        if actor.role != UserRole.ADMIN:
            await message.reply_text(messages.NO_PERMISSION)
            return
        if cmd not in {"/sayt_elave", "/sayt_sil"}:
            return
        if not value.strip():
            await message.reply_text("İstifadə: /sayt_elave knatank.az və ya /sayt_sil knatank.az")
            return
        operation = "add" if cmd == "/sayt_elave" else "remove"
        domains = await priority.update(identity.telegram_user_id, operation, value)
        await message.reply_text(
            ("Sayt əlavə edildi: " if operation == "add" else "Sayt silindi: ")
            + value.strip()
            + "\nCari prioritetlər: "
            + (", ".join(domains) if domains else "siyahı boşdur")
        )
    except InvalidPrioritySite as error:
        await message.reply_text(str(error))
    except PrioritySitePermissionDenied:
        await message.reply_text(messages.NO_PERMISSION)
    except SQLAlchemyError:
        logger.exception("Priority website configuration failed")
        await message.reply_text(messages.TEMPORARY_ERROR)


async def handle_internet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_web_command(update, context, combined=False)


async def handle_tamaxtar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_web_command(update, context, combined=True)


def _is_sales_import_request(caption: str | None) -> bool:
    """No invoice spreadsheet is treated as revenue without an explicit import request."""

    if not caption:
        return False
    text = " ".join(caption.casefold().split())
    return "satış" in text and ("idxal et" in text or "bazaya yaz" in text or "əlavə et" in text)


async def _handle_sales_xlsx_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Preview a bounded, explicit invoice spreadsheet; never post at upload time."""

    message = update.effective_message
    identity = _identity(update)
    if message is None or message.document is None or identity is None or not _is_private(update):
        return
    services = _services(context)
    actor = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if actor is None or actor.status != UserStatus.ACTIVE or actor.role != UserRole.ADMIN:
        await message.reply_text("Satış fakturalarını yalnız administrator idxal edə bilər.")
        return
    if services.sales is None:
        await message.reply_text("Satış bazası aktiv deyil.")
        return
    document = message.document
    if not (document.file_name or "").casefold().endswith(".xlsx"):
        await message.reply_text("Satış idxalı üçün .xlsx Excel faylı göndərin.")
        return
    if document.file_size is not None and document.file_size > MAX_XLSX_BYTES:
        await message.reply_text("Satış Excel faylı 2 MiB limitini aşır.")
        return
    try:
        file = await context.bot.get_file(document.file_id)
        content = bytes(await file.download_as_bytearray())
        drafts = await asyncio.to_thread(parse_sales_xlsx, content)
    except SaleValidationError as error:
        await message.reply_text(f"Excel idxal edilmədi: {error}")
        return
    except (TelegramError, OSError) as error:
        logger.warning("Sales workbook preview failed | error_type=%s", type(error).__name__)
        await message.reply_text("Excel faylı oxunmadı; yenidən yoxlayın.")
        return
    data = getattr(context, "user_data", None)
    if data is None:
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    totals: dict[str, Decimal] = {}
    for draft in drafts:
        totals[draft.currency] = totals.get(draft.currency, Decimal(0)) + draft.amount
    token = secrets.token_urlsafe(12)
    data[SALES_IMPORT_KEY] = (token, monotonic() + SALES_IMPORT_TTL_SECONDS, drafts)
    total_lines = "\n".join(
        f"• {amount:,.2f} {currency}" for currency, amount in sorted(totals.items())
    )
    examples = "\n".join(
        f"• {draft.invoice_number} — {draft.customer_name}" for draft in drafts[:3]
    )
    await message.reply_text(
        f"📊 EXCEL SATIŞ İDXALI — TƏSDİQ\n\n"
        f"Faktura sayı: {len(drafts)}\n{total_lines}\n\n"
        f"İlk fakturalar:\n{examples}\n\n"
        "Yalnız tamamlanmış satış fakturaları olduqda təsdiqləyin. "
        "Bütün fakturalar birlikdə yazılır; biri təkrarlanırsa heç biri yazılmır.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Fakturaları təsdiqlə", callback_data=f"si:ok:{token}"),
                    InlineKeyboardButton("Ləğv et", callback_data=f"si:no:{token}"),
                ]
            ]
        ),
    )


async def handle_sales_import_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only, bounded, one-use Excel batch approval."""

    identity = _identity(update)
    query = update.callback_query
    if identity is None or query is None or not _is_private(update):
        return
    await query.answer()
    data = getattr(context, "user_data", None)
    if data is None:
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    saved: object = data.pop(SALES_IMPORT_KEY, None)
    saved_items: tuple[object, ...] = (
        cast(tuple[object, ...], saved) if isinstance(saved, tuple) else ()
    )
    choice = (query.data or "").split(":")
    if (
        len(saved_items) != 3
        or len(choice) != 3
        or choice[0] != "si"
        or choice[1] not in {"ok", "no"}
        or saved_items[0] != choice[2]
        or not isinstance(saved_items[1], (int, float))
        or monotonic() > saved_items[1]
        or not isinstance(saved_items[2], tuple)
    ):
        await query.edit_message_text("Bu idxal təsdiqi artıq etibarlı deyil.")
        return
    if choice[1] == "no":
        await query.edit_message_text("Excel idxalı ləğv edildi. Satış bazası dəyişmədi.")
        return
    services = _services(context)
    actor = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if actor is None or actor.status != UserStatus.ACTIVE or actor.role != UserRole.ADMIN:
        await query.edit_message_text(messages.NO_PERMISSION)
        return
    if services.sales is None:
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    drafts = cast(tuple[SaleDraft, ...], saved_items[2])
    try:
        written = await services.sales.record_many(actor, drafts)
    except (SaleAlreadyExists, SaleValidationError) as error:
        await query.edit_message_text(f"İdxal rədd edildi: {error}")
        return
    except AuthorizationDenied:
        await query.edit_message_text(messages.NO_PERMISSION)
        return
    except SQLAlchemyError:
        logger.exception("Sales Excel import transaction failed")
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    await query.edit_message_text(
        f"✅ İDXAL TAMAMLANDI\n\n{len(written)} təsdiqlənmiş faktura "
        "satış jurnalına əlavə edildi. Hesabatı «Satış hesabatı» bölməsindən soruşun."
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    document = message.document
    if document is None:
        return
    if await _route_caption_command(update, context):
        return
    if _is_sales_import_request(message.caption):
        await _handle_sales_xlsx_upload(update, context)
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


def _is_photo_lookup_request(caption: str | None) -> bool:
    """Product-photo questions (including an uncaptained photo) are read-only.

    Persisting a picture must remain an explicit action. A silent photo upload is not
    informed consent to insert potentially confidential data into company knowledge.
    """

    if not caption or not caption.strip():
        return True
    text = " ".join(caption.casefold().split())
    # A user labeling an image as a company reference must keep the separate
    # upload/confirmation path; the recognition path must never swallow it.
    if (
        isinstance(
            parse_reference_confirmation(caption),
            (ProductPictureConfirmation, AmbiguousPictureConfirmation),
        )
        or _is_save_intent(caption)
        or _note_payload(caption) is not None
    ):
        return False
    return (
        "?" in text
        or _explicit_pdf_photo_lookup(caption)
        or any(
            phrase in text
            for phrase in (
                "bizdə var",
                "bizde var",
                "bazada var",
                "bu məhsul var",
                "bu mehsul var",
                "şəkli yoxla",
                "sekli yoxla",
                "şəkildə nədir",
                "sekilde nedir",
                "məhsulu axtar",
                "mehsulu axtar",
            )
        )
    )


def _explicit_pdf_photo_lookup(caption: str | None) -> bool:
    """A PDF visual suggestion is not evidence of an approved catalog SKU.

    Only a deliberate request for a PDF source page may enter this weaker
    path; the default 'bizdə varmı?' photo path must never return a PDF
    illustration as a company product match.
    """

    if not caption:
        return False
    phrase = " ".join(caption.casefold().split())
    return any(part in phrase for part in ("pdf", "kataloq şəkli", "kataloqdakı şəkil"))


async def handle_catalog_product_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only catalog identity preview. Never create stock, price or photo."""

    identity = _identity(update)
    message = update.effective_message
    if not _is_private(update) or identity is None or message is None:
        return
    services = _services(context)
    user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if user is None or user.status != UserStatus.ACTIVE or user.role != UserRole.ADMIN:
        await message.reply_text(messages.NO_PERMISSION)
        return
    if services.session_factory is None:
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    argument = _command_text(message).partition(" ")[2].strip()
    try:
        draft = parse_product_registration(argument)
    except ValueError as error:
        await message.reply_text(str(error))
        return
    data = getattr(context, "user_data", None)
    if data is None:
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    token = secrets.token_urlsafe(12)
    data[PRODUCT_REGISTRATION_KEY] = (token, monotonic() + PRODUCT_REGISTRATION_TTL_SECONDS, draft)
    await message.reply_text(
        f"📦 YENİ MƏHSUL QEYDİ\\n\\nKod: {draft.code}\\nAd: {draft.name}\\n\\n"
        "Bu yalnız məhsulun kataloq kimliyini yaradacaq. "
        "Qiymət, stok və şəkil avtomatik təsdiqlənmir.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Təsdiqlə", callback_data=f"pa:ok:{token}"),
                    InlineKeyboardButton("Ləğv et", callback_data=f"pa:no:{token}"),
                ]
            ]
        ),
    )


async def handle_catalog_product_add_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Single-use, expiring confirm with fresh persisted admin authorization."""

    query = update.callback_query
    identity = _identity(update)
    if not _is_private(update) or query is None or identity is None:
        return
    await query.answer()
    data = getattr(context, "user_data", None)
    if data is None:
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    raw: object = data.pop(PRODUCT_REGISTRATION_KEY, None)
    parts = (query.data or "").split(":")
    if (
        not isinstance(raw, tuple)
        or len(raw) != 3
        or len(parts) != 3
        or parts[0] != "pa"
        or parts[1] not in {"ok", "no"}
        or raw[0] != parts[2]
        or not isinstance(raw[1], (int, float))
        or monotonic() > raw[1]
        or not isinstance(raw[2], ProductRegistrationDraft)
    ):
        await query.edit_message_text("Təsdiqin vaxtı bitib və ya artıq istifadə edilib.")
        return
    if parts[1] == "no":
        await query.edit_message_text("Məhsulun yaradılması ləğv edildi.")
        return
    services = _services(context)
    if services.session_factory is None:
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if user is None or user.status != UserStatus.ACTIVE or user.role != UserRole.ADMIN:
        await query.edit_message_text(messages.NO_PERMISSION)
        return
    draft = raw[2]
    try:
        product = await ProductService(services.session_factory).create_product(
            identity.telegram_user_id, display_code=draft.code, name=draft.name
        )
    except DomainConflict:
        await query.edit_message_text("Bu məhsul kodu artıq mövcuddur; yeni qeyd yaradılmadı.")
        return
    except (AuthorizationDenied, SQLAlchemyError, ValueError) as error:
        logger.warning("Catalog product create refused | error_type=%s", type(error).__name__)
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    await query.edit_message_text(
        f"✅ MƏHSUL KATALOQA ƏLAVƏ EDİLDİ\\n\\n"
        f"Kod: {product.display_code}\\nAd: {product.name}\\n\\n"
        "İndi həmin məhsul kodunu yazaraq foto göndərin: "
        f"«Bu {product.display_code}-dir, yadda saxla». "
        "Şəkli ayrıca /saxla ilə təsdiqləyin. Qiymət və stok yaradılmadı."
    )


async def _reply_pdf_catalog_candidates(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    services: TelegramServices,
    actor_telegram_user_id: int,
    image_bytes: bytes,
) -> bool:
    """Source-page fallback only; PDF images never become approved products."""

    storage = services.storage
    if services.session_factory is None or storage is None:
        return False
    async with services.session_factory() as session:
        rows = (
            await session.execute(
                select(
                    ImageAssetOccurrence.id,
                    ImageAssetOccurrence.source_file_id,
                    FileObject.original_filename,
                    FileObject.storage_key,
                    ImageAssetOccurrence.locator,
                    ImageAsset.storage_key,
                    ImageAsset.content_sha256,
                )
                .select_from(ImageAssetOccurrence)
                .join(ImageAsset, ImageAsset.id == ImageAssetOccurrence.image_asset_id)
                .join(FileObject, FileObject.id == ImageAssetOccurrence.source_file_id)
                .where(
                    ImageAssetOccurrence.origin_type == ImageAssetOrigin.PDF_EMBEDDED,
                    FileObject.state == FileObjectState.ACTIVE,
                )
                .order_by(ImageAssetOccurrence.id.desc())
                .limit(MAX_PDF_REFERENCES + 1)
            )
        ).all()
    sources: list[PdfImageSource] = []
    original_keys: dict[int, tuple[str, bool]] = {}
    for occurrence_id, file_id, filename, original_key, locator, key, digest in rows[
        :MAX_PDF_REFERENCES
    ]:
        original_keys[occurrence_id] = (original_key, "form_xobject" in locator)
        page = locator.get("page")
        index = locator.get("image_index")
        if (
            isinstance(page, int)
            and not isinstance(page, bool)
            and isinstance(index, int)
            and not isinstance(index, bool)
        ):
            sources.append(
                PdfImageSource(
                    occurrence_id, filename, page, index, key, digest, source_file_id=file_id
                )
            )
    if not sources:
        return False
    try:
        matches = await asyncio.wait_for(
            asyncio.to_thread(match_pdf_image_sources, image_bytes, sources, storage.root),
            timeout=5.0,
        )
    except TimeoutError:
        logger.info("PDF photo candidate comparison timed out")
        return False
    semantic_incomplete = False
    unavailable = False
    if not matches and services.semantic_visual is not None:
        try:
            matches, semantic_incomplete, unavailable = await asyncio.wait_for(
                asyncio.to_thread(
                    semantic_pdf_image_candidates,
                    image_bytes,
                    sources,
                    services.semantic_visual,
                    category_verifier=local_category_verifier(
                        str(services.semantic_visual.cache_root)
                    ),
                ),
                timeout=8.0,
            )
        except TimeoutError:
            unavailable = True
        if unavailable:
            logger.info("Catalog semantic image comparison unavailable")
    if not matches:
        # No PDF match is not proof the product is absent. Keep looking up the
        # confirmed-photo state instead of returning a PDF-only dead end.
        logger.info(
            "PDF visual candidate fallback found no match | model_unavailable=%s",
            unavailable,
        )
        return False

    # Resolve product text only from the original matched PDF page's native
    # drawing geometry. Never look for generic page text by SKU keywords.
    async def enrich_pdf_row(candidate: PdfPageCandidate) -> PdfPageCandidate:
        occurrence_id = candidate.occurrence_id
        if occurrence_id is None or occurrence_id not in original_keys:
            return candidate
        original_key, has_form = original_keys[occurrence_id]
        if has_form:
            return candidate
        try:
            original_path = storage.path_for_key(original_key)
            original_path.relative_to(storage.blob_root)
        except (StorageError, ValueError):
            return candidate
        try:
            details = await asyncio.wait_for(
                asyncio.to_thread(
                    read_pdf_visual_details,
                    original_path,
                    candidate.page,
                    candidate.image_index,
                ),
                timeout=2.0,
            )
        except TimeoutError:
            return candidate
        return replace(
            candidate,
            nearby_label=details.nearby_label,
            visual_row=details.visual_row,
        )

    matches = tuple(await asyncio.gather(*(enrich_pdf_row(item) for item in matches)))
    data = getattr(context, "user_data", None)
    ticket = issue_catalog_ticket(actor_telegram_user_id, matches)
    if data is not None:
        data.pop(CATALOG_SOURCE_TICKET_KEY, None)
        if ticket is not None:
            data[CATALOG_SOURCE_TICKET_KEY] = ticket
    keyboard = _catalog_source_keyboard(ticket) if data is not None else _answer_keyboard()
    await message.reply_text(
        render_pdf_image_candidates(
            matches,
            incomplete=len(rows) > MAX_PDF_REFERENCES or semantic_incomplete,
        ),
        reply_markup=keyboard,
    )
    return True


async def _handle_exact_photo_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Exact content, conservative duplicate and opt-in unverified semantic candidates."""

    message = update.effective_message
    identity = _identity(update)
    if not _is_private(update) or message is None or identity is None or not message.photo:
        return
    data = getattr(context, "user_data", None)
    if data is not None:
        data.pop(CATALOG_SOURCE_TICKET_KEY, None)
    services = _services(context)
    user = await services.users.get_by_telegram_id(identity.telegram_user_id)
    if user is None:
        await message.reply_text(messages.UNKNOWN_USER)
        return
    if user.status != UserStatus.ACTIVE:
        await message.reply_text(_status_message(user))
        return
    if services.session_factory is None or services.storage is None:
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    photo = message.photo[-1]
    if photo.file_size is not None and photo.file_size > 4 * 1024 * 1024:
        await message.reply_text("Şəkil yoxlama üçün çox böyükdür. Daha kiçik şəkil göndərin.")
        return
    try:
        telegram_file = await context.bot.get_file(photo.file_id)
        picture = bytes(await telegram_file.download_as_bytearray())
        if len(picture) > 4 * 1024 * 1024:
            await message.reply_text("Şəkil yoxlama üçün çox böyükdür.")
            return
        digest = sha256(picture).hexdigest()
        async with services.session_factory() as session:
            holder = await ProductImageRelationRepository(
                session
            ).active_confirmation_holder_by_content(digest, active_product_only=True)
            if holder is not None:
                await message.reply_text(
                    f"📦 MƏHSUL TAPILDI\n\nKod: {holder[1]}\n"
                    "Uyğunluq: eyni şəkil faylı təsdiqlənib.\n\n"
                    "Qeyd: kataloqda olması anbarda hazırda stok olduğunu sübut etmir."
                )
                return
            asset_id = await session.scalar(
                select(ImageAsset.id).where(ImageAsset.content_sha256 == digest)
            )
            # Only human-confirmed, active catalog references can become candidates.
            # A document sighting or unconfirmed image is never trusted as identity.
            rows = (
                await session.execute(
                    select(Product.display_code, ImageAsset.storage_key, ImageAsset.content_sha256)
                    .select_from(ProductImageRelation)
                    .join(Product, Product.id == ProductImageRelation.product_id)
                    .join(ImageAsset, ImageAsset.id == ProductImageRelation.image_asset_id)
                    .where(
                        ProductImageRelation.confirmation_type
                        == ProductImageRelationConfirmation.USER_EXPLICIT,
                        ProductImageRelation.superseded_at.is_(None),
                        Product.is_active.is_(True),
                    )
                    .order_by(ProductImageRelation.created_at.desc())
                    .limit(501)
                )
            ).all()
        incomplete = len(rows) > 500
        references = [
            VisualCandidate(
                product_code=code, storage_key=storage_key, content_sha256=reference_sha
            )
            for code, storage_key, reference_sha in rows[:500]
        ]
        try:
            possible = await asyncio.wait_for(
                asyncio.to_thread(shortlist, picture, references, services.storage.root),
                timeout=3.0,
            )
        except TimeoutError:
            logger.info("Confirmed-photo near-duplicate lookup timed out")
            possible = ()
        if possible:
            lines = "\n".join(f"• {item.product_code}" for item in possible)
            warning = "\nYoxlama ilk 500 təsdiqli nümunə ilə məhdudlaşıb." if incomplete else ""
            await message.reply_text(
                "📦 VİZUAL OXŞAR NÜMUNƏLƏR\n\n"
                f"{lines}\n\n"
                "Bunlar yalnız ölçüsü/keyfiyyəti dəyişmiş şəkillər üzrə "
                "ehtimal olunan uyğunluqlardır. Məhsulun eyniliyi və stok "
                "təsdiqlənməyib; kodu yoxlayın." + warning
            )
            return
        semantic_unavailable = False
        if services.semantic_visual is not None and references:
            try:
                semantic = await asyncio.wait_for(
                    asyncio.to_thread(
                        services.semantic_visual.search,
                        picture,
                        [
                            SemanticReference(code, storage_key, digest)
                            for code, storage_key, digest in rows[:500]
                        ],
                    ),
                    timeout=6.0,
                )
            except TimeoutError:
                logger.info("Confirmed-photo semantic lookup timed out")
                semantic = None
            if semantic is not None and semantic.candidates:
                lines = "\n".join(f"• {item.product_code}" for item in semantic.candidates)
                limited = (
                    "\nYoxlama yalnız seçilmiş təsdiqli nümunələri əhatə edir."
                    if incomplete or semantic.incomplete
                    else ""
                )
                await message.reply_text(
                    "📷 MƏHSUL ÜZRƏ EHTİMAL OLUNAN NAMİZƏDLƏR\n\n"
                    f"{lines}\n\n"
                    "Fərqli bucaqdan çəkilmiş şəkillərin semantik oxşarlığı əsasında "
                    "göstərilib. Bu, məhsulun eyniliyinin təsdiqi deyil. "
                    "Məhsul kodunu yoxlayın; anbarda stok olduğu da təsdiqlənmir." + limited
                )
                return
            semantic_unavailable = semantic is None or semantic.unavailable

        # PDF art is evidence of a source page, never an authoritative SKU.
        # Run this costly source search only when explicitly requested.
        if _explicit_pdf_photo_lookup(message.caption):
            if await _reply_pdf_catalog_candidates(
                message, context, services, identity.telegram_user_id, picture
            ):
                return
        if asset_id is not None:
            await message.reply_text(
                "Şəkil faylı yadda saxlanılıb, amma heç bir aktiv məhsul koduna "
                "təsdiqlənmiş nümunə kimi bağlanmayıb. Yadda saxlamaq təkbaşına "
                "məhsul yaratmır və 129 AZN kimi qiyməti təsdiqləmir. "
                "Məhsul artıq kataloqdadırsa, onun DƏQİQ kodu ilə şəkli "
                "yenidən göndərin: «Bu KA45-dir, yadda saxla». "
                "Sonra «Yadda saxla» düyməsini seçin. "
                "İstinad yoxdursa, fərqli bucaqdan dəqiq tanınma mümkün olmaya bilər."
            )
            return
        if semantic_unavailable:
            await message.reply_text(
                "Semantik şəkil modeli bu anda əlçatan deyil. "
                "Məhsulun kimliyini təxmin etmirəm; kodla axtarın."
            )
            return
        warning = " Arxivin yalnız ilk 500 nümunəsi yoxlanılıb." if incomplete else ""
        if not references:
            await message.reply_text(
                "Kataloqda aktiv məhsula bağlanmış təsdiqli foto nümunəsi yoxdur. "
                "Şəkildən məhsulun kimliyini və ya stokunu təxmin edə bilmərəm. "
                "Admin məhsulu /mehsul_elave əmri ilə qeydiyyata almalı, "
                "sonra şəkil üçün /saxla təsdiqi verməlidir. "
                "Mövcud PDF şəkillərinin axtarışı üçün fotoya "
                "«PDF kataloq şəkli axtar» qeydi əlavə edin."
            )
            return
        await message.reply_text(
            "Təsdiqlənmiş nümunələr arasında eyni və ya kifayət qədər "
            "oxşar şəkil tapılmadı. Bu, məhsulun bizdə olmadığını sübut etmir. "
            "Fərqli bucaqdan çəkilmiş şəkillər bu üsulla tanınmaya bilər. "
            "Bu şəkli məhsulla əlaqələndirmək üçün kodunu qeyd edib "
            "«Bu KA45-dir, yadda saxla» kimi yenidən göndərin." + warning
        )
    except (TelegramError, SQLAlchemyError, TimeoutError) as error:
        logger.warning("Exact photo lookup unavailable | error_type=%s", type(error).__name__)
        await message.reply_text("Şəkil axtarışı hazırda mümkün deyil; yenidən sınayın.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.photo:
        return
    if await _route_caption_command(update, context):
        return
    if _is_photo_lookup_request(message.caption):
        _discard_menu_selection(context)
        await _handle_exact_photo_lookup(update, context)
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


def _revoke_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(messages.REFERENCE_REVOKE_YES_BUTTON, callback_data="ri:yes"),
                InlineKeyboardButton(messages.REFERENCE_REVOKE_NO_BUTTON, callback_data="ri:no"),
            ]
        ]
    )


async def handle_revoke_reference(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask the administrator to confirm before any picture claim is retired (§23.7).

    Nothing is read from the database to build this prompt. The product is resolved by the
    callback, after the confirmation, so the administrator is shown the code they typed and the
    count only ever comes from the write itself.
    """

    if not _is_private(update):
        return
    resolved = await _active_user(update, context)
    if resolved is None:
        return
    services, user, message = resolved
    if services.reference_images is None:
        await message.reply_text(messages.TEMPORARY_ERROR)
        return
    # Refusing here rather than at the callback keeps a privileged prompt away from someone who
    # could never answer it; the service still checks, because the role may change in between.
    if not is_admin(user):
        await message.reply_text(messages.NO_PERMISSION)
        return
    argument = _command_text(message).partition(" ")[2].strip()
    if not argument:
        await message.reply_text(messages.REFERENCE_REVOKE_USAGE)
        return
    try:
        canonical_code = normalize_product_code(argument.split()[0])
    except ValueError:
        # A code that normalizes to nothing cannot name a product, so it names nothing instead.
        await message.reply_text(messages.REFERENCE_IMAGE_PRODUCT_NOT_FOUND.format(code=argument))
        return
    services.reference_revokes.request(user.telegram_user_id, canonical_code)
    await message.reply_text(
        messages.REFERENCE_REVOKE_CONFIRM.format(code=canonical_code),
        reply_markup=_revoke_keyboard(),
    )


async def handle_revoke_reference_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Carry out, or drop, the revocation the administrator was just asked about.

    The code comes from server-side state rather than ``query.data``, because callback payloads are
    client-supplied and an unverified string gets to decide whose confirmation disappears.
    """

    query = update.callback_query
    actor = update.effective_user
    if query is None or actor is None or not _is_private(update):
        return
    await query.answer()
    services = _services(context)
    if services.reference_images is None:
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    canonical_code = services.reference_revokes.code_for(actor.id)
    if canonical_code is None:
        await query.edit_message_text(messages.REFERENCE_REVOKE_NO_REQUEST)
        return
    services.reference_revokes.clear(actor.id)
    if query.data == "ri:no":
        await query.edit_message_text(messages.REFERENCE_REVOKE_CANCELLED)
        return
    if query.data != "ri:yes":
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    try:
        result = await services.reference_images.revoke_product_picture(
            telegram_user_id=actor.id, canonical_code=canonical_code
        )
    except AuthorizationDenied:
        await query.edit_message_text(messages.NO_PERMISSION)
        return
    except (ApplicationError, SQLAlchemyError, StorageError, OSError, ValueError):
        logger.exception("Reference image revocation failed | code=%s", canonical_code)
        await query.edit_message_text(messages.TEMPORARY_ERROR)
        return
    await query.edit_message_text(_reference_revoke_reply(result, canonical_code))


def _reference_revoke_reply(result: ReferenceRevokeResult, code: str) -> str:
    """Answer a revocation in Azerbaijani, naming the product and how many claims it took."""

    if result.status is ReferenceRevokeStatus.REVOKED:
        return messages.REFERENCE_REVOKE_DONE.format(
            code=result.display_code or code, count=result.revoked_count
        )
    if result.status is ReferenceRevokeStatus.NOTHING_TO_REVOKE:
        return messages.REFERENCE_REVOKE_NOTHING.format(code=result.display_code or code)
    return messages.REFERENCE_IMAGE_PRODUCT_NOT_FOUND.format(code=code)


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
        + (messages.FILE_DUPLICATE if result.duplicate else ""),
        reply_markup=_upload_keyboard(),
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
        messages.WHOAMI_DETAILS.format(
            telegram_user_id=user.telegram_user_id, role=user.role, status=user.status
        )
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
        await message.reply_text(messages.USERS_NONE_REGISTERED)
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
    "stok": handle_stock_command,
    "menu": handle_menu,
    "whoami": handle_whoami,
    "users": handle_users,
    "cancel": handle_cancel,
    "saxla": handle_save,
    "hatirlatmalar": handle_reminders,
    "help": handle_help,
    "verilerimi_sil": handle_delete_data,
    "leqv_et": handle_revoke_reference,
    "mehsul_elave": handle_catalog_product_add,
    "baza": handle_baza,
    "internet": handle_internet,
    "saytlar": handle_priority_sites,
    "sayt_elave": handle_priority_sites,
    "sayt_sil": handle_priority_sites,
    "tamaxtar": handle_tamaxtar,
}
