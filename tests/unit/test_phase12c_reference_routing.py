"""Reference image routing: only the naming caption reaches the confirmation service (§23.7)."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import MessageEntity, Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.ingestion import (
    IngestionSessionService,
    SessionSummary,
)
from office_assistant.application.reference_images import (
    ReferenceImageDenialReason,
    ReferenceImageResult,
    ReferenceImageSaveDenied,
    ReferenceImageService,
    ReferenceImageStatus,
)
from office_assistant.application.user_management import UserManagementService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.handlers import (
    SERVICES_KEY,
    TelegramServices,
    handle_photo,
    handle_save,
)
from office_assistant.integrations.telegram.state import (
    PasswordAttemptLimiter,
    PasswordEntryState,
)

MAX_UPLOAD_BYTES = 10_000
NAMING_CAPTION = "Bu KA45-dir, yadda saxla"


@dataclass
class FakeTelegramFile:
    async def download_to_drive(self, custom_path: str | Path) -> Path:
        path = Path(custom_path)
        path.write_bytes(b"placeholder upload body")
        return path


@dataclass
class FakePhotoSize:
    file_id: str = "photo-1"
    file_unique_id: str = "photo-unique"
    width: int = 800
    height: int = 600
    file_size: int | None = 512


@dataclass
class FakeMessage:
    caption: str | None = None
    text: str | None = None
    caption_entities: tuple[MessageEntity, ...] = ()
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
    get_file_calls: list[str] = field(default_factory=list[str])

    async def get_file(self, file_id: str) -> FakeTelegramFile:
        self.get_file_calls.append(file_id)
        return FakeTelegramFile()

    async def send_chat_action(self, **kwargs: object) -> None:
        return None


@dataclass
class FakeContext:
    bot_data: dict[str, object]
    bot: FakeBot


def _user() -> User:
    return User(
        id=1,
        telegram_user_id=123,
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
        timezone="Asia/Baku",
    )


def _ingestion(
    *, commit_result: SessionSummary | None = None, commit_error: Exception | None = None
) -> Any:
    return SimpleNamespace(
        max_upload_bytes=MAX_UPLOAD_BYTES,
        add_file=AsyncMock(
            return_value=SimpleNamespace(
                summary=SimpleNamespace(file_count=1, instruction_count=0), duplicate=False
            )
        ),
        add_instruction=AsyncMock(),
        commit=AsyncMock(return_value=commit_result, side_effect=commit_error),
        cancel=AsyncMock(return_value=False),
    )


def _reference(
    result: ReferenceImageResult | None = None, *, side_effect: Exception | None = None
) -> tuple[ReferenceImageService, AsyncMock]:
    queue = AsyncMock(
        return_value=result
        if result is not None
        else ReferenceImageResult(ReferenceImageStatus.QUEUED, display_code="KA45"),
        side_effect=side_effect,
    )
    return cast(ReferenceImageService, SimpleNamespace(record_product_picture=queue)), queue


def _services(
    root: Path,
    *,
    ingestion: Any = None,
    reference_images: ReferenceImageService | None = None,
) -> TelegramServices:
    return TelegramServices(
        authentication=cast(AuthenticationService, SimpleNamespace()),
        users=cast(
            UserManagementService,
            SimpleNamespace(
                get_by_telegram_id=AsyncMock(return_value=_user()),
                touch_active_user=AsyncMock(),
            ),
        ),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        ingestion=cast(IngestionSessionService, ingestion or _ingestion()),
        storage=LocalFileStorage(root),
        session_factory=cast(async_sessionmaker[AsyncSession], SimpleNamespace()),
        reference_images=reference_images,
    )


def _photo_update(
    caption: str | None,
    *,
    chat_type: str = ChatType.PRIVATE,
    command_entity: bool = False,
) -> FakeUpdate:
    entities: tuple[MessageEntity, ...] = ()
    if command_entity and caption is not None and caption.startswith("/"):
        entities = (_command_entity(caption.split()[0]),)
    return FakeUpdate(
        effective_user=TelegramUser(123, "Test", False, username="test"),
        effective_chat=FakeChat(type=chat_type),
        effective_message=FakeMessage(
            caption=caption, caption_entities=entities, photo=(FakePhotoSize(),)
        ),
    )


def _command_entity(command: str, offset: int = 0) -> MessageEntity:
    return MessageEntity(offset=offset, length=len(command), type=MessageEntity.BOT_COMMAND)


def _run(services: TelegramServices, update: FakeUpdate) -> FakeMessage:
    context = FakeContext(bot_data={SERVICES_KEY: services}, bot=FakeBot())
    asyncio.run(handle_photo(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)))
    message = update.effective_message
    assert message is not None
    return message


def _run_save(services: TelegramServices) -> FakeMessage:
    message = FakeMessage(text="/saxla")
    update = FakeUpdate(
        effective_user=TelegramUser(123, "Test", False, username="test"),
        effective_chat=FakeChat(),
        effective_message=message,
    )
    context = FakeContext(bot_data={SERVICES_KEY: services}, bot=FakeBot())
    asyncio.run(handle_save(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)))
    return message


@pytest.mark.parametrize(
    "caption",
    [
        NAMING_CAPTION,
        "KA45 şəklini yadda saxla",
        "Bu şəkli KA45 məhsuluna bağla",
        "KA45 üçün referans şəkil kimi saxla",
        "Bu KA45 məhsulunun şəklidir, yadda saxla",
    ],
)
def test_the_naming_caption_queues_the_picture_it_carried(tmp_path: Path, caption: str) -> None:
    ingestion = _ingestion()
    reference_images, queue = _reference()
    services = _services(tmp_path, ingestion=ingestion, reference_images=reference_images)
    message = _run(services, _photo_update(caption))

    call = queue.await_args
    assert call is not None
    assert call.kwargs == {
        "telegram_user_id": 123,
        "telegram_chat_id": 5,
        "telegram_message_id": 77,
        "canonical_code": "KA45",
    }
    # The picture is stored first: the queued claim speaks about the file this message carried.
    assert ingestion.add_file.await_count == 1
    assert message.replies[-1] == messages.REFERENCE_IMAGE_QUEUED.format(code="KA45")


def test_an_ambiguous_caption_answers_and_queues_nothing(tmp_path: Path) -> None:
    reference_images, queue = _reference()
    services = _services(tmp_path, reference_images=reference_images)
    message = _run(services, _photo_update("Bu KA45-dir, KA46 deyil, yadda saxla"))

    assert queue.await_count == 0
    assert messages.REFERENCE_IMAGE_AMBIGUOUS in message.replies


@pytest.mark.parametrize(
    "caption",
    [
        "Qiymət siyahısıdır",
        "Bu şəkli yadda saxla",
        "Bu məhsul KA45, yadda saxla",
        "Bu KA45-dir",
        "KA45 şəklini saxlama",
    ],
)
def test_an_ordinary_caption_never_reaches_the_reference_service(
    tmp_path: Path, caption: str | None
) -> None:
    ingestion = _ingestion()
    reference_images, queue = _reference()
    services = _services(tmp_path, ingestion=ingestion, reference_images=reference_images)
    message = _run(services, _photo_update(caption))

    assert queue.await_count == 0
    assert ingestion.add_file.await_count == 1
    assert message.replies == [messages.FILE_ADDED.format(files=1, instructions=0)]


@pytest.mark.parametrize("caption", [None, "KA45 nədir, yadda saxla?"])
def test_uncaptained_or_question_photo_is_read_only(
    tmp_path: Path, caption: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from office_assistant.integrations.telegram import handlers

    lookup = AsyncMock()
    monkeypatch.setattr(handlers, "_handle_exact_photo_lookup", lookup)
    ingestion = _ingestion()
    reference_images, queue = _reference()
    services = _services(tmp_path, ingestion=ingestion, reference_images=reference_images)
    _run(services, _photo_update(caption))
    lookup.assert_awaited_once()
    ingestion.add_file.assert_not_awaited()
    queue.assert_not_awaited()


def test_a_command_caption_keeps_its_documented_routing(tmp_path: Path) -> None:
    """An explicit command still beats ingestion, so there is no stored picture to queue (§22)."""

    reference_images, queue = _reference()
    services = _services(tmp_path, reference_images=reference_images)
    message = _run(services, _photo_update("/help Bu KA45-dir, yadda saxla", command_entity=True))

    assert queue.await_count == 0
    assert message.replies[0] == messages.CAPTION_COMMAND_FILE_SKIPPED


def test_a_group_photo_queues_nothing(tmp_path: Path) -> None:
    reference_images, queue = _reference()
    services = _services(tmp_path, reference_images=reference_images)
    message = _run(services, _photo_update(NAMING_CAPTION, chat_type=ChatType.GROUP))

    assert queue.await_count == 0
    assert message.replies == []


def test_without_the_service_a_naming_caption_is_harmless(tmp_path: Path) -> None:
    message = _run(_services(tmp_path), _photo_update(NAMING_CAPTION))

    assert message.replies == [messages.FILE_ADDED.format(files=1, instructions=0)]


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            ReferenceImageResult(ReferenceImageStatus.QUEUED, display_code="KA45"),
            messages.REFERENCE_IMAGE_QUEUED.format(code="KA45"),
        ),
        (
            ReferenceImageResult(ReferenceImageStatus.PRODUCT_NOT_FOUND),
            messages.REFERENCE_IMAGE_PRODUCT_NOT_FOUND.format(code="KA45"),
        ),
        (
            ReferenceImageResult(ReferenceImageStatus.ALREADY_LINKED, display_code="KA45"),
            messages.REFERENCE_IMAGE_ALREADY_SAVED.format(code="KA45"),
        ),
        (
            ReferenceImageResult(ReferenceImageStatus.CONFLICT, display_code="KA99"),
            messages.REFERENCE_IMAGE_CONFLICT.format(code="KA99"),
        ),
        (
            ReferenceImageResult(ReferenceImageStatus.NO_PICTURE),
            messages.REFERENCE_IMAGE_NO_PICTURE,
        ),
        (
            ReferenceImageResult(ReferenceImageStatus.SESSION_CLOSED),
            messages.REFERENCE_IMAGE_NO_SESSION,
        ),
    ],
)
def test_every_outcome_answers_in_azerbaijani(
    tmp_path: Path, result: ReferenceImageResult, expected: str
) -> None:
    reference_images, _ = _reference(result)
    services = _services(tmp_path, reference_images=reference_images)
    message = _run(services, _photo_update(NAMING_CAPTION))

    assert message.replies[-1] == expected


def test_a_denied_actor_is_told_without_crashing(tmp_path: Path) -> None:
    reference_images, _ = _reference(
        side_effect=AuthorizationDenied("Operation requires an active user")
    )
    services = _services(tmp_path, reference_images=reference_images)
    message = _run(services, _photo_update(NAMING_CAPTION))

    assert message.replies[-1] == messages.NO_PERMISSION


@pytest.mark.parametrize(
    "failure",
    [SQLAlchemyError("database is down"), OSError("disk is full"), ValueError("bad bytes")],
)
def test_a_failed_confirmation_degrades_instead_of_crashing(
    tmp_path: Path, failure: Exception
) -> None:
    reference_images, _ = _reference(side_effect=failure)
    services = _services(tmp_path, reference_images=reference_images)
    message = _run(services, _photo_update(NAMING_CAPTION))

    assert message.replies[-1] == messages.TEMPORARY_ERROR


def _summary(*, linked: int) -> SessionSummary:
    return SessionSummary(
        session_id=7,
        file_count=1,
        instruction_count=0,
        duplicate_count=0,
        job_id=11,
        reference_images_linked=linked,
    )


def test_saxla_names_the_picture_relation_it_committed(tmp_path: Path) -> None:
    services = _services(tmp_path, ingestion=_ingestion(commit_result=_summary(linked=1)))
    message = _run_save(services)

    assert message.replies == [
        messages.SAVE_SUCCESS.format(files=1, instructions=0, duplicates=0)
        + messages.REFERENCE_IMAGE_LINKED_NOTE.format(count=1)
    ]


def test_saxla_without_a_queued_picture_keeps_its_old_answer(tmp_path: Path) -> None:
    services = _services(tmp_path, ingestion=_ingestion(commit_result=_summary(linked=0)))
    message = _run_save(services)

    assert message.replies == [messages.SAVE_SUCCESS.format(files=1, instructions=0, duplicates=0)]


def test_saxla_names_the_conflict_that_stopped_the_save(tmp_path: Path) -> None:
    denied = ReferenceImageSaveDenied(
        ReferenceImageDenialReason.CONFLICT, canonical_code="KA45", holder_code="KA99"
    )
    services = _services(tmp_path, ingestion=_ingestion(commit_error=denied))
    message = _run_save(services)

    # The rollback means nothing was stored, so the reply says which claim blocked it.
    assert message.replies == [messages.REFERENCE_IMAGE_SAVE_CONFLICT.format(code="KA99")]


def test_saxla_says_so_when_the_queued_picture_cannot_be_read(tmp_path: Path) -> None:
    denied = ReferenceImageSaveDenied(
        ReferenceImageDenialReason.UNREADABLE_PICTURE, canonical_code="KA45"
    )
    services = _services(tmp_path, ingestion=_ingestion(commit_error=denied))
    message = _run_save(services)

    assert message.replies == [messages.REFERENCE_IMAGE_UNREADABLE]
