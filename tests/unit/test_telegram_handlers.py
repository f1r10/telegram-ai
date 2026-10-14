import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from telegram import Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.error import NetworkError
from telegram.ext import ContextTypes

from office_assistant.ai.service import InProcessRateGuard, RetrievalUnavailable
from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.user_management import UserManagementService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.integrations.telegram.app import create_telegram_application
from office_assistant.integrations.telegram.handlers import (
    TelegramServices,
    _reply_long,  # pyright: ignore[reportPrivateUsage]
    _try_file_delivery,  # pyright: ignore[reportPrivateUsage]
    _user_label,  # pyright: ignore[reportPrivateUsage]
    handle_baza,
    handle_internet,
    handle_password_text,
    handle_start,
    handle_tamaxtar,
    handle_users,
    handle_whoami,
)
from office_assistant.integrations.telegram.messages import (
    AUTH_PROMPT,
    COMBINED_INTERNAL_UNAVAILABLE,
    HELP_TEXT,
    QDRANT_UNAVAILABLE,
    USERS_LINE,
    USERS_NO_NAME,
    USERS_NO_USERNAME,
    USERS_NONE_REGISTERED,
    WELCOME,
    WHOAMI_DETAILS,
)
from office_assistant.integrations.telegram.state import PasswordAttemptLimiter, PasswordEntryState
from office_assistant.internal_evidence.delivery import DeliverableFile, SourceFileDeliveryService
from office_assistant.web_evidence.models import WebAnswer


@dataclass
class FakeMessage:
    text: str | None = None
    replies: list[str] = field(default_factory=lambda: list[str]())

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append(text)


@dataclass
class FakeChat:
    type: str = ChatType.PRIVATE


@dataclass
class FakeUpdate:
    effective_user: TelegramUser
    effective_chat: FakeChat
    effective_message: FakeMessage


@dataclass
class FakeContext:
    bot_data: dict[str, object]


class FakeUsers:
    def __init__(self, user: User | None) -> None:
        self.user = user
        self.touched = False
        self.registered: list[User] = []

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        return self.user

    async def touch_active_user(self, telegram_user_id: int) -> None:
        self.touched = True

    async def list_users_for_actor(self, telegram_user_id: int) -> list[User]:
        return self.registered


def _services(users: FakeUsers) -> TelegramServices:
    return TelegramServices(
        authentication=cast(AuthenticationService, object()),
        users=cast(UserManagementService, users),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
    )


def _plain_message(text: str, **overrides: object) -> SimpleNamespace:
    """A text-only turn. A real Telegram message always carries the media fields below."""
    fields: dict[str, object] = {
        "text": text,
        "chat_id": 123,
        "message_id": 77,
        "reply_text": AsyncMock(),
        "photo": None,
        "document": None,
        "reply_to_message": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


async def _run_start(users: FakeUsers, message: FakeMessage) -> None:
    update = FakeUpdate(
        effective_user=TelegramUser(123, "Test", False, username="test"),
        effective_chat=FakeChat(),
        effective_message=message,
    )
    context = FakeContext(bot_data={"phase2_services": _services(users)})
    await handle_start(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))


def test_start_prompts_unknown_private_user() -> None:
    import asyncio

    message = FakeMessage()
    asyncio.run(_run_start(FakeUsers(None), message))
    assert message.replies == [AUTH_PROMPT]


def test_start_welcomes_active_user_without_password() -> None:
    import asyncio

    user = User(telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)
    message = FakeMessage()
    users = FakeUsers(user)
    asyncio.run(_run_start(users, message))
    assert message.replies == [WELCOME]
    assert users.touched


def test_telegram_application_constructs_without_network() -> None:
    application = create_telegram_application("test-token", _services(FakeUsers(None)))

    assert sum(len(group) for group in application.handlers.values()) == 25
    assert application.error_handlers


@pytest.mark.parametrize("grounded", [False, True])
def test_typing_network_failure_does_not_discard_question(grounded: bool) -> None:
    user = User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)
    services = _services(FakeUsers(user))
    answer = SimpleNamespace(answer="answer", insufficient_evidence=False, sources=[])
    ai = SimpleNamespace(
        answer_general=AsyncMock(return_value=answer),
        answer_baza=AsyncMock(return_value=answer),
    )
    services.ai = ai  # type: ignore[assignment]
    services.rate_guard = InProcessRateGuard()
    internal = SimpleNamespace(
        answer=AsyncMock(
            return_value=SimpleNamespace(answer="answer", citations=[], route="INTERNAL_HYBRID")
        )
    )
    if grounded:
        services.internal_evidence = internal  # type: ignore[assignment]
    message = _plain_message("/baza question" if grounded else "question")
    update = FakeUpdate(TelegramUser(123, "Test", False), FakeChat(), cast(FakeMessage, message))
    context = SimpleNamespace(
        bot_data={"phase2_services": services},
        bot=SimpleNamespace(send_chat_action=AsyncMock(side_effect=NetworkError("offline"))),
    )
    handler = handle_baza if grounded else handle_password_text
    asyncio.run(handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)))
    if grounded:
        internal.answer.assert_awaited_once_with(
            user, "question", force_internal=True, image_readings=[]
        )
    else:
        ai.answer_general.assert_awaited_once_with(1, "question")
    message.reply_text.assert_awaited_once()


def test_reply_retries_only_failed_part(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "office_assistant.integrations.telegram.handlers.asyncio.sleep", AsyncMock()
    )
    message = SimpleNamespace(reply_text=AsyncMock(side_effect=[NetworkError("offline"), None]))
    asyncio.run(_reply_long(message, "answer", 4096))
    assert message.reply_text.await_count == 2


def test_reply_network_retries_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "office_assistant.integrations.telegram.handlers.asyncio.sleep", AsyncMock()
    )
    message = SimpleNamespace(reply_text=AsyncMock(side_effect=NetworkError("offline")))
    with pytest.raises(NetworkError):
        asyncio.run(_reply_long(message, "answer", 4096))
    assert message.reply_text.await_count == 3


def test_internet_command_uses_only_web_service() -> None:
    user = User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)
    services = _services(FakeUsers(user))
    services.rate_guard = InProcessRateGuard()
    services.web_evidence = cast(
        object,
        SimpleNamespace(answer_internet=AsyncMock(return_value=WebAnswer("web answer"))),
    )  # type: ignore[assignment]
    services.internal_evidence = cast(
        object, SimpleNamespace(answer=AsyncMock(side_effect=AssertionError("internal called")))
    )  # type: ignore[assignment]
    message = _plain_message("/internet KA45")
    update = FakeUpdate(TelegramUser(123, "Test", False), FakeChat(), cast(FakeMessage, message))
    context = SimpleNamespace(
        bot_data={"phase2_services": services},
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
    )
    asyncio.run(handle_internet(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)))
    services.web_evidence.answer_internet.assert_awaited_once_with(user, "KA45")  # type: ignore[union-attr]
    services.internal_evidence.answer.assert_not_called()  # type: ignore[union-attr]


def _run_handler(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]],
    services: TelegramServices,
    text: str,
) -> SimpleNamespace:
    message = _plain_message(text)
    update = FakeUpdate(TelegramUser(123, "Test", False), FakeChat(), cast(FakeMessage, message))
    context = SimpleNamespace(
        bot_data={"phase2_services": services},
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
    )
    asyncio.run(handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)))
    return message


@pytest.mark.parametrize(
    ("handler", "text"),
    [
        (handle_baza, "/baza KA45 zəmanət müddəti nə qədərdir?"),
        (handle_password_text, "KA45 zəmanət müddəti nə qədərdir?"),
    ],
)
def test_broken_index_is_reported_as_unavailable_not_as_no_match(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]], text: str
) -> None:
    user = User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)
    services = _services(FakeUsers(user))
    services.rate_guard = InProcessRateGuard()
    services.internal_evidence = cast(
        object,
        SimpleNamespace(answer=AsyncMock(side_effect=RetrievalUnavailable("projection missing"))),
    )  # type: ignore[assignment]

    message = _run_handler(handler, services, text)
    assert message.reply_text.await_args.args[0] == QDRANT_UNAVAILABLE  # type: ignore[union-attr]


def test_combined_command_names_the_unavailable_internal_source() -> None:
    user = User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)
    services = _services(FakeUsers(user))
    services.rate_guard = InProcessRateGuard()
    services.web_evidence = cast(
        object,
        SimpleNamespace(
            answer_combined=AsyncMock(side_effect=RetrievalUnavailable("projection missing"))
        ),
    )  # type: ignore[assignment]

    message = _run_handler(handle_tamaxtar, services, "/tamaxtar KA45-in qiyməti")
    assert message.reply_text.await_args.args[0] == COMBINED_INTERNAL_UNAVAILABLE  # type: ignore[union-attr]


def test_file_follow_up_sends_original_bytes_and_safe_filename(tmp_path: Path) -> None:
    content = b"original-pdf-bytes"
    path = tmp_path / "blob.pdf"
    path.write_bytes(content)

    class Delivery:
        async def resolve_recent(
            self, actor_telegram_user_id: int, citation_index: int | None
        ) -> DeliverableFile:
            assert actor_telegram_user_id == 123
            assert citation_index == 2
            return DeliverableFile(path, "source.pdf", "application/pdf", 55)

    received: dict[str, object] = {}

    class Message:
        async def reply_document(self, document: object, **kwargs: object) -> None:
            received["bytes"] = document.read()  # type: ignore[attr-defined]
            received["filename"] = kwargs["filename"]

        async def reply_text(self, text: str) -> None:
            raise AssertionError(text)

    services = _services(FakeUsers(None))
    services.file_delivery = cast(SourceFileDeliveryService, Delivery())
    handled = asyncio.run(_try_file_delivery(Message(), services, 123, "2-ci mənbəni göndər"))
    assert handled
    assert received == {"bytes": content, "filename": "source.pdf"}


def test_colloquial_file_request_completes_the_turn_without_calling_the_model(
    tmp_path: Path,
) -> None:
    path = tmp_path / "catalog.pdf"
    path.write_bytes(b"catalog-bytes")

    class Delivery:
        async def resolve_recent(
            self, actor_telegram_user_id: int, citation_index: int | None
        ) -> DeliverableFile:
            assert citation_index is None
            return DeliverableFile(path, "catalog.pdf", "application/pdf", 55)

    user = User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)
    services = _services(FakeUsers(user))
    services.file_delivery = cast(SourceFileDeliveryService, Delivery())
    services.rate_guard = InProcessRateGuard()
    # Without the FILE_DELIVERY verb, this turn reached the evidence planner instead.
    services.internal_evidence = cast(
        object,
        SimpleNamespace(answer=AsyncMock(side_effect=AssertionError("evidence model was called"))),
    )  # type: ignore[assignment]
    message = SimpleNamespace(
        text="Faylı ver",
        chat_id=123,
        reply_text=AsyncMock(side_effect=AssertionError("answered with text")),
        reply_document=AsyncMock(),
    )
    update = FakeUpdate(TelegramUser(123, "Test", False), FakeChat(), cast(FakeMessage, message))
    context = SimpleNamespace(
        bot_data={"phase2_services": services},
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
    )
    asyncio.run(
        handle_password_text(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))
    )
    assert message.reply_document.await_args.kwargs["filename"] == "catalog.pdf"  # type: ignore[union-attr]
    services.internal_evidence.answer.assert_not_called()  # type: ignore[union-attr]


def test_whoami_answers_from_the_message_catalog() -> None:
    """No handler composes user-facing text inline; the catalog is the only source."""

    user = User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)
    message = _run_handler(handle_whoami, _services(FakeUsers(user)), "/kim")

    call = message.reply_text.await_args
    assert call is not None
    assert call.args[0] == WHOAMI_DETAILS.format(
        telegram_user_id=user.telegram_user_id, role=user.role, status=user.status
    )


def test_an_empty_user_list_answers_from_the_message_catalog() -> None:
    user = User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)
    message = _run_handler(handle_users, _services(FakeUsers(user)), "/istifadəçilər")

    call = message.reply_text.await_args
    assert call is not None
    assert call.args[0] == USERS_NONE_REGISTERED


@pytest.mark.parametrize(
    ("first_name", "last_name", "username"),
    [
        (None, None, None),
        ("Aysel", "Məmmədova", "aysel"),
        ("Aysel", None, None),
    ],
)
def test_user_list_label_uses_message_catalog(
    first_name: str | None, last_name: str | None, username: str | None
) -> None:
    user = User(
        telegram_user_id=123,
        role=UserRole.USER,
        status=UserStatus.ACTIVE,
        first_name=first_name,
        last_name=last_name,
        telegram_username=username,
    )
    expected = USERS_LINE.format(
        telegram_user_id=123,
        name=" ".join(part for part in (first_name, last_name) if part) or USERS_NO_NAME,
        username=f"@{username}" if username else USERS_NO_USERNAME,
        status=UserStatus.ACTIVE,
    )
    assert _user_label(user) == expected


def test_nonempty_admin_user_list_uses_message_catalog() -> None:
    user = User(telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)
    users = FakeUsers(user)
    users.registered = [user]
    message = _run_handler(handle_users, _services(users), "/users")
    assert message.reply_text.await_args.args[0] == _user_label(user)  # type: ignore[union-attr]


def test_help_describes_combined_search_and_source_followup_separately() -> None:
    assert "/tamaxtar <sual>" in HELP_TEXT
    assert "/tamaxtar <nömrə>" not in HELP_TEXT
    assert "2-ci mənbəni göndər" in HELP_TEXT
