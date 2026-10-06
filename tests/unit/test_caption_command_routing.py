"""Caption command routing: an explicit command beats default file ingestion."""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Message, MessageEntity, Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.ingestion import IngestionSessionService
from office_assistant.application.user_management import UserManagementService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.handlers import (
    COMMAND_CALLBACKS,
    SERVICES_KEY,
    TelegramServices,
    _caption_command,  # pyright: ignore[reportPrivateUsage]
    handle_document,
    handle_photo,
)
from office_assistant.integrations.telegram.state import (
    PasswordAttemptLimiter,
    PasswordEntryState,
)
from office_assistant.internal_evidence.models import InternalAnswer, RoutingCategory
from office_assistant.internal_evidence.service import InternalEvidenceService

MAX_UPLOAD_BYTES = 10_000


@dataclass
class FakeTelegramFile:
    staging: list[Path] = field(default_factory=lambda: list[Path]())

    async def download_to_drive(self, custom_path: str | Path) -> Path:
        path = Path(custom_path)
        self.staging.append(path)
        path.write_bytes(b"placeholder upload body")
        return path


@dataclass
class FakeDocument:
    file_id: str = "doc-1"
    file_name: str | None = "qiymet-siyahisi.pdf"
    mime_type: str | None = "application/pdf"
    file_size: int | None = 512


@dataclass
class FakePhotoSize:
    file_id: str = "photo-1"
    file_unique_id: str = "photo-unique"
    width: int = 800
    height: int = 600
    file_size: int | None = 512


@dataclass
class FakeMessage:
    text: str | None = None
    caption: str | None = None
    caption_entities: tuple[MessageEntity, ...] = ()
    document: FakeDocument | None = None
    photo: tuple[FakePhotoSize, ...] = ()
    message_id: int = 77
    chat_id: int = 5
    replies: list[str] = field(default_factory=list[str])

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append(text)


@dataclass
class FakeChat:
    type: str = ChatType.PRIVATE
    id: int = 5


@dataclass
class FakeUpdate:
    effective_user: TelegramUser
    effective_chat: FakeChat
    effective_message: FakeMessage | None = None
    callback_query: None = None
    update_id: int = 1
    effective_id: int = 5


@dataclass
class FakeBot:
    files: FakeTelegramFile = field(default_factory=FakeTelegramFile)
    get_file_calls: list[str] = field(default_factory=list[str])

    async def get_file(self, file_id: str) -> FakeTelegramFile:
        self.get_file_calls.append(file_id)
        return self.files

    async def send_chat_action(self, **kwargs: object) -> None:
        return None


@dataclass
class FakeContext:
    bot_data: dict[str, object]
    bot: FakeBot


def _command_entity(command: str, offset: int = 0) -> MessageEntity:
    return MessageEntity(offset=offset, length=len(command), type=MessageEntity.BOT_COMMAND)


def _user() -> User:
    return User(
        id=1,
        telegram_user_id=123,
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
        timezone="Asia/Baku",
    )


def _ingestion() -> Any:
    return SimpleNamespace(
        max_upload_bytes=MAX_UPLOAD_BYTES,
        add_file=AsyncMock(
            return_value=SimpleNamespace(
                summary=SimpleNamespace(file_count=1, instruction_count=0), duplicate=False
            )
        ),
        add_instruction=AsyncMock(),
        commit=AsyncMock(return_value=None),
        cancel=AsyncMock(return_value=False),
    )


def _services(
    root: Path,
    *,
    user: User | None = None,
    ingestion: Any = None,
    internal_evidence: InternalEvidenceService | None = None,
) -> TelegramServices:
    active = user if user is not None else _user()
    return TelegramServices(
        authentication=cast(AuthenticationService, SimpleNamespace()),
        users=cast(
            UserManagementService,
            SimpleNamespace(
                get_by_telegram_id=AsyncMock(return_value=active),
                touch_active_user=AsyncMock(),
            ),
        ),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        ingestion=cast(IngestionSessionService, ingestion or _ingestion()),
        storage=LocalFileStorage(root),
        session_factory=cast(async_sessionmaker[AsyncSession], SimpleNamespace()),
        internal_evidence=internal_evidence,
    )


def _context(services: TelegramServices) -> FakeContext:
    return FakeContext(bot_data={SERVICES_KEY: services}, bot=FakeBot())


def _run(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]],
    update: FakeUpdate,
    services: TelegramServices,
) -> FakeContext:
    context = _context(services)
    asyncio.run(handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)))
    return context


def _leading_command_entities(caption: str | None) -> tuple[MessageEntity, ...]:
    if caption is None or not caption.startswith("/"):
        return ()
    return (_command_entity(caption.split()[0]),)


def _document_update(caption: str | None) -> FakeUpdate:
    message = FakeMessage(
        caption=caption,
        caption_entities=_leading_command_entities(caption),
        document=FakeDocument(),
    )
    return FakeUpdate(
        effective_user=TelegramUser(123, "Test", False, username="test"),
        effective_chat=FakeChat(),
        effective_message=message,
    )


def _photo_update(caption: str) -> FakeUpdate:
    message = FakeMessage(
        caption=caption,
        caption_entities=_leading_command_entities(caption),
        photo=(FakePhotoSize(),),
    )
    return FakeUpdate(
        effective_user=TelegramUser(123, "Test", False, username="test"),
        effective_chat=FakeChat(),
        effective_message=message,
    )


def test_document_caption_command_skips_ingestion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routed = AsyncMock()
    monkeypatch.setitem(COMMAND_CALLBACKS, "baza", routed)
    ingestion = _ingestion()
    update = _document_update("/baza Qiymət necədir?")
    context = _run(handle_document, update, _services(tmp_path, ingestion=ingestion))
    message = update.effective_message
    assert message is not None
    assert routed.await_count == 1
    assert ingestion.add_file.await_count == 0
    assert context.bot.get_file_calls == []
    assert message.replies == [messages.CAPTION_COMMAND_FILE_SKIPPED]


def test_caption_arguments_reach_the_command(tmp_path: Path) -> None:
    answer = InternalAnswer(
        answer="Zəmanət 24 aydır.",
        route=RoutingCategory.INTERNAL_DOCUMENT,
    )
    answer_call = AsyncMock(return_value=answer)
    internal = cast(InternalEvidenceService, SimpleNamespace(answer=answer_call))
    update = _document_update("/baza Zəmanət neçə aydır?")
    services = _services(tmp_path, internal_evidence=internal)
    message = update.effective_message
    assert message is not None
    context = _run(handle_document, update, services)
    assert answer_call.await_count == 1
    call = answer_call.await_args
    assert call is not None
    assert call.args[1] == "Zəmanət neçə aydır?"
    assert call.kwargs["force_internal"] is True
    assert message.replies[0] == messages.CAPTION_COMMAND_FILE_SKIPPED
    assert answer.answer in message.replies[1]
    assert context.bot.get_file_calls == []


def test_photo_caption_routes_to_a_no_argument_command(tmp_path: Path) -> None:
    update = _photo_update("/help@office_assistant_bot")
    context = _run(handle_photo, update, _services(tmp_path))
    message = update.effective_message
    assert message is not None
    assert message.replies[0] == messages.CAPTION_COMMAND_FILE_SKIPPED
    assert messages.HELP_TEXT in "".join(message.replies)
    assert context.bot.get_file_calls == []


def test_unknown_slash_caption_still_ingests_the_file(tmp_path: Path) -> None:
    ingestion = _ingestion()
    update = _document_update("/tap 10 10")
    context = _run(handle_document, update, _services(tmp_path, ingestion=ingestion))
    message = update.effective_message
    assert message is not None
    assert ingestion.add_file.await_count == 1
    assert context.bot.get_file_calls == ["doc-1"]
    assert messages.FILE_ADDED.format(files=1, instructions=0) in message.replies[-1]


def test_file_without_caption_still_ingests(tmp_path: Path) -> None:
    ingestion = _ingestion()
    update = _document_update(None)
    _run(handle_document, update, _services(tmp_path, ingestion=ingestion))
    message = update.effective_message
    assert message is not None
    assert ingestion.add_file.await_count == 1
    assert message.replies == [messages.FILE_ADDED.format(files=1, instructions=0)]


def test_prose_caption_still_ingests_the_file(tmp_path: Path) -> None:
    ingestion = _ingestion()
    update = _document_update("Qiymət siyahısıdır")
    _run(handle_document, update, _services(tmp_path, ingestion=ingestion))
    assert ingestion.add_file.await_count == 1


def test_command_must_start_the_caption(tmp_path: Path) -> None:
    caption = "Sənəd /baza sorğusu"
    update = _document_update(caption)
    message = update.effective_message
    assert message is not None
    message.caption_entities = (_command_entity("/baza", offset=caption.index("/baza")),)
    assert _caption_command(cast(Message, message)) is None
    ingestion = _ingestion()
    _run(handle_document, update, _services(tmp_path, ingestion=ingestion))
    assert ingestion.add_file.await_count == 1
