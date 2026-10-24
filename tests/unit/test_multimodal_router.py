"""An image attached to a command, or replied to, is a search parameter of that turn."""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Message, MessageEntity, Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from office_assistant.ai.provider import LanguageModel, ModelResponse, TokenUsage
from office_assistant.ai.schemas import GroundedModelOutput
from office_assistant.ai.service import GroundedAnswerService, InProcessRateGuard
from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.ingestion import IngestionSessionService
from office_assistant.application.user_management import UserManagementService
from office_assistant.business_tools.registry import BusinessToolRegistry
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.handlers import (
    SERVICES_KEY,
    TelegramServices,
    handle_password_text,
    handle_photo,
)
from office_assistant.integrations.telegram.state import (
    PasswordAttemptLimiter,
    PasswordEntryState,
)
from office_assistant.integrations.telegram.turn_context import (
    MediaKind,
    build_turn_context,
    message_media,
)
from office_assistant.internal_evidence.document_search import InternalDocumentSearch
from office_assistant.internal_evidence.models import (
    EvidencePlan,
    ImageReading,
    InternalAnswer,
    RoutingCategory,
)
from office_assistant.internal_evidence.service import (
    NOT_FOUND_INTERNAL_AZ,
    InternalEvidenceService,
    _retrieval_query,  # pyright: ignore[reportPrivateUsage]
    build_evidence_bundle,
)
from office_assistant.processing.vision import ImageTranscription, VisionUnavailable

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PRICE_TEXT = "KA45 — 125 AZN"
QUESTION = "Bu şəkildəki qiymət neçəyədir?"


@dataclass
class FakeTelegramFile:
    body: bytes = JPEG_BYTES

    async def download_as_bytearray(self) -> bytearray:
        return bytearray(self.body)

    async def download_to_drive(self, custom_path: str | Path) -> Path:
        path = Path(custom_path)
        path.write_bytes(self.body)
        return path


@dataclass
class FakePhotoSize:
    file_id: str = "photo-1"
    file_unique_id: str = "photo-unique"
    width: int = 800
    height: int = 600
    file_size: int | None = 512


@dataclass
class FakeDocument:
    file_id: str = "doc-1"
    file_name: str | None = "sened.pdf"
    mime_type: str | None = "application/pdf"
    file_size: int | None = 512


@dataclass
class FakeMessage:
    text: str | None = None
    caption: str | None = None
    caption_entities: tuple[MessageEntity, ...] = ()
    photo: tuple[FakePhotoSize, ...] = ()
    document: FakeDocument | None = None
    message_id: int = 77
    chat_id: int = 5
    reply_to_message: Any = None
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
    body: bytes = JPEG_BYTES
    fail_download: bool = False

    async def get_file(self, file_id: str) -> FakeTelegramFile:
        self.get_file_calls.append(file_id)
        if self.fail_download:
            raise RuntimeError("telegram file lookup failed")
        return FakeTelegramFile(body=self.body)

    async def send_chat_action(self, **kwargs: object) -> None:
        return None


@dataclass
class FakeContext:
    bot_data: dict[str, object]
    bot: FakeBot


class FakeImageReader:
    def __init__(
        self, transcription: ImageTranscription | None = None, error: Exception | None = None
    ) -> None:
        self.transcription = transcription
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def read(self, **kwargs: object) -> ImageTranscription:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.transcription is not None
        return self.transcription


def _reading_transcription() -> ImageTranscription:
    return ImageTranscription(
        text=PRICE_TEXT,
        description="Qiymət siyahısı",
        model="gpt-4o-mini",
        media_type="image/jpeg",
        byte_size=len(JPEG_BYTES),
    )


def _user() -> User:
    return User(id=1, telegram_user_id=123, role=UserRole.ADMIN, status=UserStatus.ACTIVE)


def _ingestion() -> Any:
    return SimpleNamespace(
        max_upload_bytes=10_000,
        has_open_session=AsyncMock(return_value=False),
        add_file=AsyncMock(
            return_value=SimpleNamespace(
                summary=SimpleNamespace(file_count=1, instruction_count=0), duplicate=False
            )
        ),
        add_instruction=AsyncMock(),
        commit=AsyncMock(
            return_value=SimpleNamespace(
                file_count=1, instruction_count=0, duplicate_count=0, reference_images_linked=0
            )
        ),
    )


def _services(
    tmp_path: Path,
    *,
    ingestion: Any = None,
    internal_evidence: InternalEvidenceService | None = None,
    image_reader: FakeImageReader | None = None,
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
        storage=LocalFileStorage(tmp_path),
        session_factory=cast(async_sessionmaker[AsyncSession], SimpleNamespace()),
        rate_guard=InProcessRateGuard(100),
        internal_evidence=internal_evidence,
        image_reader=cast(Any, image_reader),
    )


def _command_entity(command: str) -> MessageEntity:
    return MessageEntity(offset=0, length=len(command), type=MessageEntity.BOT_COMMAND)


def _photo_message(caption: str | None, **kwargs: Any) -> FakeMessage:
    return FakeMessage(
        caption=caption,
        caption_entities=(_command_entity(caption.split()[0]),)
        if caption and caption.startswith("/")
        else (),
        photo=(FakePhotoSize(),),
        **kwargs,
    )


def _update(message: FakeMessage) -> FakeUpdate:
    return FakeUpdate(
        effective_user=TelegramUser(123, "Test", False, username="test"),
        effective_chat=FakeChat(),
        effective_message=message,
    )


def _evidence_service() -> tuple[Any, AsyncMock]:
    answer = InternalAnswer(
        answer="Şəkildəki qiymət 125 AZN-dir.", route=RoutingCategory.INTERNAL_DOCUMENT
    )
    answer_call = AsyncMock(return_value=answer)
    return SimpleNamespace(answer=answer_call), answer_call


def _run(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]],
    update: FakeUpdate,
    services: TelegramServices,
    bot: FakeBot | None = None,
) -> FakeContext:
    context = FakeContext(bot_data={SERVICES_KEY: services}, bot=bot or FakeBot())
    asyncio.run(handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)))
    return context


def test_media_is_collected_from_the_message_and_the_message_it_replies_to() -> None:
    replied = FakeMessage(photo=(FakePhotoSize(file_id="photo-old"),), message_id=70)
    message = FakeMessage(
        text="Bu neçəyədir?", photo=(FakePhotoSize(file_id="photo-new"),), reply_to_message=replied
    )

    turn = build_turn_context(cast(Message, message), text="Bu neçəyədir?")

    assert [item.file_id for item in turn.images] == ["photo-new", "photo-old"]
    assert turn.text == "Bu neçəyədir?"


def test_a_replied_document_is_media_but_not_an_image() -> None:
    replied = FakeMessage(document=FakeDocument())
    message = FakeMessage(text="mövcuddur?", reply_to_message=replied)

    media = message_media(cast(Message, replied))
    turn = build_turn_context(cast(Message, message), text="mövcuddur?")

    assert media[0].kind is MediaKind.DOCUMENT
    assert turn.media[0].file_id == "doc-1"
    assert turn.images == ()


def test_baza_caption_keeps_the_photo_and_reads_it_for_the_question(tmp_path: Path) -> None:
    internal, answer_call = _evidence_service()
    reader = FakeImageReader(_reading_transcription())
    message = _photo_message(f"/baza {QUESTION}")
    update = _update(message)
    ingestion = _ingestion()
    context = _run(
        handle_photo,
        update,
        _services(tmp_path, ingestion=ingestion, internal_evidence=internal, image_reader=reader),
    )

    assert ingestion.add_file.await_count == 0
    assert messages.CAPTION_COMMAND_FILE_SKIPPED not in message.replies
    assert context.bot.get_file_calls == ["photo-1"]
    assert reader.calls[0]["image_bytes"] == JPEG_BYTES
    call = answer_call.await_args
    assert call is not None
    assert call.args[1] == QUESTION
    readings = list(call.kwargs["image_readings"])
    assert [item.text for item in readings] == [PRICE_TEXT]
    assert [item.filename for item in readings] == ["telegram_photo_77.jpg"]
    assert "Şəkildəki qiymət 125 AZN-dir." in message.replies[-1]


def test_a_plain_reply_to_a_photo_carries_the_picture_into_the_answer(tmp_path: Path) -> None:
    internal, answer_call = _evidence_service()
    reader = FakeImageReader(_reading_transcription())
    replied = FakeMessage(photo=(FakePhotoSize(file_id="photo-old"),), message_id=70)
    message = FakeMessage(text="Bu şəkildəki məhsulun qiyməti neçədir?", reply_to_message=replied)

    _run(
        handle_password_text,
        _update(message),
        _services(tmp_path, internal_evidence=internal, image_reader=reader),
    )

    call = answer_call.await_args
    assert call is not None
    assert [item.text for item in call.kwargs["image_readings"]] == [PRICE_TEXT]
    assert reader.calls[0]["media_type"] == "image/jpeg"


def test_an_unreadable_photo_halts_deictic_answer_without_inventing_content(tmp_path: Path) -> None:
    internal, answer_call = _evidence_service()
    reader = FakeImageReader(error=VisionUnavailable("vision vendor is down"))
    message = _photo_message(f"/baza {QUESTION}")

    _run(
        handle_photo,
        _update(message),
        _services(tmp_path, internal_evidence=internal, image_reader=reader),
    )

    assert messages.IMAGE_READING_UNAVAILABLE in message.replies[0]
    assert answer_call.await_count == 0
    assert messages.TEMPORARY_ERROR not in "".join(message.replies)


def test_image_bytes_that_cannot_be_fetched_still_degrade_in_azerbaijani(
    tmp_path: Path,
) -> None:
    internal, _ = _evidence_service()
    reader = FakeImageReader(_reading_transcription())
    message = _photo_message("/baza Şəkil nə deyir?")

    _run(
        handle_photo,
        _update(message),
        _services(tmp_path, internal_evidence=internal, image_reader=reader),
        bot=FakeBot(fail_download=True),
    )

    assert messages.IMAGE_NOT_READ in message.replies[0]
    assert reader.calls == []


def test_missing_reader_is_reported_instead_of_silently_ignoring_the_photo(
    tmp_path: Path,
) -> None:
    internal, answer_call = _evidence_service()
    message = _photo_message(f"/baza {QUESTION}")

    _run(handle_photo, _update(message), _services(tmp_path, internal_evidence=internal))

    assert messages.IMAGE_READING_UNAVAILABLE in message.replies[0]
    assert answer_call.await_count == 0


def test_saxla_caption_stores_the_file_and_then_commits_it(tmp_path: Path) -> None:
    ingestion = _ingestion()
    message = _photo_message("/saxla")

    _run(handle_photo, _update(message), _services(tmp_path, ingestion=ingestion))

    assert ingestion.add_file.await_count == 1
    assert ingestion.commit.await_count == 1
    saved = messages.SAVE_SUCCESS.format(files=1, instructions=0, duplicates=0)
    assert saved in message.replies[-1]


# --- evidence layer: the picture is data, never a company source -------------------------


class _Planner:
    def __init__(self, evidence_plan: EvidencePlan) -> None:
        self.evidence_plan = evidence_plan

    async def plan(self, user_id: int, question: str, **kwargs: object) -> object:
        return SimpleNamespace(plan=self.evidence_plan, response=None)


class _NoSearch:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, value: object) -> list[object]:
        self.queries.append(str(cast(Any, value).query))
        return []


class _SynthesisModel:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, **kwargs: Any) -> ModelResponse:
        self.prompts.append(str(kwargs["input_messages"][0]["content"]))
        return ModelResponse(
            parsed=GroundedModelOutput(answer="Şəkildəki qiymət 125 AZN-dir.", citations=["E1"]),
            model="stub",
            response_id=None,
            usage=TokenUsage(),
            latency_ms=1,
        )


class _Harness(InternalEvidenceService):
    async def _persist_turn(self, *args: object, **kwargs: object) -> None:
        return None

    async def _save_usage(self, *args: object, **kwargs: object) -> None:
        return None


def _evidence_harness() -> tuple[_Harness, _NoSearch, _SynthesisModel]:
    service = _Harness.__new__(_Harness)
    service.session_factory = cast(async_sessionmaker[AsyncSession], SimpleNamespace())
    service.registry = cast(BusinessToolRegistry, SimpleNamespace(names=()))
    search = _NoSearch()
    service.document_search = cast(InternalDocumentSearch, search)
    service.planner = cast(Any, _Planner(_internal_plan()))
    model = _SynthesisModel()
    service.language_model = cast(LanguageModel, model)
    service.general_service = cast(GroundedAnswerService, SimpleNamespace())
    service.document_limit = 5
    service.source_ttl = timedelta(minutes=60)
    service.source_context_messages = 6
    service.max_tool_calls = 4
    return service, search, model


def _internal_plan() -> EvidencePlan:
    return EvidencePlan(
        category=RoutingCategory.INTERNAL_DOCUMENT,
        intent="PRODUCT_PRICE",
        use_structured_tools=False,
        use_document_search=True,
        reason_code="",
    )


def _reading() -> ImageReading:
    return ImageReading(filename="telegram_photo_77.jpg", text=PRICE_TEXT, description="Siyahı")


def test_picture_text_expands_the_retrieval_query_but_stays_bounded() -> None:
    long_text = "qiymət " * 500

    query = _retrieval_query("KA45 nə qədərdir?", [ImageReading(filename="a.jpg", text=long_text)])

    assert query.startswith("KA45 nə qədərdir? ")
    assert len(query) <= len("KA45 nə qədərdir?") + 301


def test_a_turn_without_an_image_keeps_the_plain_query() -> None:
    assert _retrieval_query("KA45 nə qədərdir?", []) == "KA45 nə qədərdir?"


def test_image_text_is_cited_as_a_picture_and_never_as_a_file() -> None:
    bundle = build_evidence_bundle([], [], None, [_reading()])

    citation = bundle.citations[0]
    assert citation.source_class == "IMAGE_UNTRUSTED"
    assert citation.file_id is None
    assert citation.locator is None
    assert "Şəkil" in citation.label
    assert PRICE_TEXT in bundle.evidence_payloads[citation.evidence_id]
    assert bundle.image_results[0].filename == "telegram_photo_77.jpg"


def test_a_photo_alone_is_not_reported_as_absent_from_the_system() -> None:
    service, search, model = _evidence_harness()

    result = asyncio.run(service.answer(_user(), QUESTION, image_readings=[_reading()]))

    assert result.answer != NOT_FOUND_INTERNAL_AZ
    assert result.citations[0].source_class == "IMAGE_UNTRUSTED"
    assert PRICE_TEXT in search.queries[0]
    assert "IMAGE_UNTRUSTED" in model.prompts[0]


def test_without_a_picture_the_absence_answer_is_unchanged() -> None:
    service, _, _ = _evidence_harness()

    result = asyncio.run(service.answer(_user(), "Belə məhsul yoxdur"))

    assert result.answer == NOT_FOUND_INTERNAL_AZ
    assert result.insufficient_evidence is True
