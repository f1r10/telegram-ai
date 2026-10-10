"""``/leqv_et`` asks an administrator before it retires anything, and trusts no button (§23.7).

The revocation is a privileged write, so the interesting properties are the ones around it: the
product is decided by the server, the prompt is refused for anyone who is not an administrator, and
"Xeyr" leaves the database untouched.
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import InlineKeyboardMarkup, Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.reference_images import (
    ReferenceImageService,
    ReferenceRevokeResult,
    ReferenceRevokeStatus,
)
from office_assistant.application.user_management import UserManagementService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.app import create_telegram_application
from office_assistant.integrations.telegram.handlers import (
    SERVICES_KEY,
    TelegramServices,
    handle_revoke_reference,
    handle_revoke_reference_callback,
)
from office_assistant.integrations.telegram.state import (
    PasswordAttemptLimiter,
    PasswordEntryState,
    ReferenceRevokeState,
)

ADMIN_TELEGRAM_ID = 123


@dataclass
class FakeMessage:
    text: str | None = None
    message_id: int = 7
    chat_id: int = 5
    replies: list[str] = field(default_factory=list[str])
    markups: list[object] = field(default_factory=list[object])

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append(text)
        self.markups.append(kwargs.get("reply_markup"))


@dataclass
class FakeChat:
    type: str = ChatType.PRIVATE
    id: int = 5


@dataclass
class FakeQuery:
    data: str | None
    answered: bool = False
    edited: list[str] = field(default_factory=list[str])

    async def answer(self) -> None:
        self.answered = True

    async def edit_message_text(self, text: str, **kwargs: object) -> None:
        self.edited.append(text)


@dataclass
class FakeUpdate:
    effective_user: TelegramUser
    effective_chat: FakeChat
    effective_message: FakeMessage | None = None
    callback_query: FakeQuery | None = None
    update_id: int = 1
    effective_id: int = 5


@dataclass
class FakeContext:
    bot_data: dict[str, object]


def _user(*, admin: bool = True) -> User:
    return User(
        id=4,
        telegram_user_id=ADMIN_TELEGRAM_ID,
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN if admin else UserRole.USER,
        timezone="Asia/Baku",
    )


def _revoker(
    result: ReferenceRevokeResult | None = None, *, side_effect: Exception | None = None
) -> tuple[ReferenceImageService, AsyncMock]:
    revoke = AsyncMock(
        return_value=result
        if result is not None
        else ReferenceRevokeResult(
            ReferenceRevokeStatus.REVOKED, display_code="KA45", revoked_count=1
        ),
        side_effect=side_effect,
    )
    return cast(ReferenceImageService, SimpleNamespace(revoke_product_picture=revoke)), revoke


def _services(
    root: Path,
    *,
    user: User | None = None,
    reference_images: ReferenceImageService | None = None,
    revokes: ReferenceRevokeState | None = None,
) -> TelegramServices:
    return TelegramServices(
        authentication=cast(AuthenticationService, SimpleNamespace()),
        users=cast(
            UserManagementService,
            SimpleNamespace(
                get_by_telegram_id=AsyncMock(return_value=user if user is not None else _user()),
                touch_active_user=AsyncMock(),
            ),
        ),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        storage=LocalFileStorage(root),
        session_factory=cast(async_sessionmaker[AsyncSession], SimpleNamespace()),
        reference_images=reference_images,
        reference_revokes=revokes or ReferenceRevokeState(),
    )


def _run(handler: Any, update: FakeUpdate, services: TelegramServices) -> None:
    context = FakeContext(bot_data={SERVICES_KEY: services})
    asyncio.run(handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context)))


def _command(text: str, *, chat_type: str = ChatType.PRIVATE) -> FakeUpdate:
    return FakeUpdate(
        effective_user=TelegramUser(ADMIN_TELEGRAM_ID, "Test", False, username="test"),
        effective_chat=FakeChat(type=chat_type),
        effective_message=FakeMessage(text=text),
    )


def _callback(data: str) -> FakeUpdate:
    return FakeUpdate(
        effective_user=TelegramUser(ADMIN_TELEGRAM_ID, "Test", False, username="test"),
        effective_chat=FakeChat(),
        callback_query=FakeQuery(data=data),
    )


def _ask(services: TelegramServices, text: str) -> FakeMessage:
    update = _command(text)
    _run(handle_revoke_reference, update, services)
    message = update.effective_message
    assert message is not None
    return message


def _press(services: TelegramServices, data: str) -> FakeQuery:
    update = _callback(data)
    _run(handle_revoke_reference_callback, update, services)
    query = update.callback_query
    assert query is not None
    assert query.answered
    return query


def _button_data(markup: object) -> list[str]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [
        str(getattr(button, "callback_data", "") or "")
        for row in markup.inline_keyboard
        for button in row
    ]


def test_the_command_asks_first_and_writes_nothing(tmp_path: Path) -> None:
    reference_images, revoke = _revoker()
    revokes = ReferenceRevokeState()
    services = _services(tmp_path, reference_images=reference_images, revokes=revokes)
    message = _ask(services, "/leqv_et KA45")

    assert revoke.await_count == 0
    assert message.replies == [messages.REFERENCE_REVOKE_CONFIRM.format(code="KA45")]
    assert _button_data(message.markups[0]) == ["ri:yes", "ri:no"]
    assert revokes.code_for(ADMIN_TELEGRAM_ID) == "KA45"


def test_the_code_is_normalised_before_it_is_held(tmp_path: Path) -> None:
    reference_images, revoke = _revoker()
    revokes = ReferenceRevokeState()
    services = _services(tmp_path, reference_images=reference_images, revokes=revokes)

    _ask(services, "/leqv_et ka-45")

    assert revoke.await_count == 0
    assert revokes.code_for(ADMIN_TELEGRAM_ID) == "KA45"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/leqv_et", messages.REFERENCE_REVOKE_USAGE),
        ("/leqv_et   ", messages.REFERENCE_REVOKE_USAGE),
        (
            "/leqv_et ---",
            messages.REFERENCE_IMAGE_PRODUCT_NOT_FOUND.format(code="---"),
        ),
    ],
)
def test_an_argument_that_names_no_code_prompts_no_revocation(
    tmp_path: Path, text: str, expected: str
) -> None:
    reference_images, revoke = _revoker()
    revokes = ReferenceRevokeState()
    services = _services(tmp_path, reference_images=reference_images, revokes=revokes)
    message = _ask(services, text)

    assert revoke.await_count == 0
    assert message.replies == [expected]
    assert revokes.code_for(ADMIN_TELEGRAM_ID) is None


def test_a_non_administrator_is_refused_before_the_prompt(tmp_path: Path) -> None:
    reference_images, revoke = _revoker()
    revokes = ReferenceRevokeState()
    services = _services(
        tmp_path,
        user=_user(admin=False),
        reference_images=reference_images,
        revokes=revokes,
    )
    message = _ask(services, "/leqv_et KA45")

    assert revoke.await_count == 0
    assert message.replies == [messages.NO_PERMISSION]
    assert revokes.code_for(ADMIN_TELEGRAM_ID) is None


def test_a_group_command_prompts_nothing(tmp_path: Path) -> None:
    reference_images, revoke = _revoker()
    services = _services(tmp_path, reference_images=reference_images)
    update = _command("/leqv_et KA45", chat_type=ChatType.GROUP)
    message = update.effective_message
    assert message is not None

    _run(handle_revoke_reference, update, services)

    assert revoke.await_count == 0
    assert message.replies == []


def test_without_the_service_the_command_degrades(tmp_path: Path) -> None:
    message = _ask(_services(tmp_path), "/leqv_et KA45")

    assert message.replies == [messages.TEMPORARY_ERROR]


def test_yes_revokes_with_the_held_code(tmp_path: Path) -> None:
    reference_images, revoke = _revoker(
        ReferenceRevokeResult(ReferenceRevokeStatus.REVOKED, display_code="KA45", revoked_count=2)
    )
    revokes = ReferenceRevokeState()
    services = _services(tmp_path, reference_images=reference_images, revokes=revokes)
    _ask(services, "/leqv_et KA45")

    query = _press(services, "ri:yes")

    call = revoke.await_args
    assert call is not None
    # The product comes from server-side state: a callback payload is client-supplied and could
    # otherwise name a different confirmation than the one the administrator was shown.
    assert call.kwargs == {"telegram_user_id": ADMIN_TELEGRAM_ID, "canonical_code": "KA45"}
    assert query.edited == [messages.REFERENCE_REVOKE_DONE.format(code="KA45", count=2)]
    assert revokes.code_for(ADMIN_TELEGRAM_ID) is None


def test_no_writes_nothing(tmp_path: Path) -> None:
    reference_images, revoke = _revoker()
    revokes = ReferenceRevokeState()
    services = _services(tmp_path, reference_images=reference_images, revokes=revokes)
    _ask(services, "/leqv_et KA45")

    query = _press(services, "ri:no")

    assert revoke.await_count == 0
    assert query.edited == [messages.REFERENCE_REVOKE_CANCELLED]
    assert revokes.code_for(ADMIN_TELEGRAM_ID) is None


def test_a_press_without_an_open_prompt_writes_nothing(tmp_path: Path) -> None:
    reference_images, revoke = _revoker()
    services = _services(tmp_path, reference_images=reference_images)

    query = _press(services, "ri:yes")

    assert revoke.await_count == 0
    assert query.edited == [messages.REFERENCE_REVOKE_NO_REQUEST]


def test_an_unexpected_button_writes_nothing(tmp_path: Path) -> None:
    reference_images, revoke = _revoker()
    services = _services(tmp_path, reference_images=reference_images)
    _ask(services, "/leqv_et KA45")

    query = _press(services, "ri:maybe")

    assert revoke.await_count == 0
    assert query.edited == [messages.TEMPORARY_ERROR]


def test_a_second_press_of_the_same_button_does_not_revoke_again(tmp_path: Path) -> None:
    """A prompt is one decision: the confirmation is consumed, not left open to be pressed again."""

    reference_images, revoke = _revoker()
    services = _services(tmp_path, reference_images=reference_images)
    _ask(services, "/leqv_et KA45")

    _press(services, "ri:yes")
    again = _press(services, "ri:yes")

    assert revoke.await_count == 1
    assert again.edited == [messages.REFERENCE_REVOKE_NO_REQUEST]


def test_a_group_callback_writes_nothing(tmp_path: Path) -> None:
    reference_images, revoke = _revoker()
    services = _services(tmp_path, reference_images=reference_images)
    _ask(services, "/leqv_et KA45")
    update = FakeUpdate(
        effective_user=TelegramUser(ADMIN_TELEGRAM_ID, "Test", False, username="test"),
        effective_chat=FakeChat(type=ChatType.GROUP),
        callback_query=FakeQuery(data="ri:yes"),
    )

    _run(handle_revoke_reference_callback, update, services)

    assert revoke.await_count == 0


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            ReferenceRevokeResult(
                ReferenceRevokeStatus.REVOKED, display_code="KA45", revoked_count=1
            ),
            messages.REFERENCE_REVOKE_DONE.format(code="KA45", count=1),
        ),
        (
            ReferenceRevokeResult(ReferenceRevokeStatus.NOTHING_TO_REVOKE, display_code="KA45"),
            messages.REFERENCE_REVOKE_NOTHING.format(code="KA45"),
        ),
        (
            ReferenceRevokeResult(ReferenceRevokeStatus.PRODUCT_NOT_FOUND),
            messages.REFERENCE_IMAGE_PRODUCT_NOT_FOUND.format(code="KA45"),
        ),
    ],
)
def test_every_outcome_answers_in_azerbaijani(
    tmp_path: Path, result: ReferenceRevokeResult, expected: str
) -> None:
    reference_images, _ = _revoker(result)
    services = _services(tmp_path, reference_images=reference_images)
    _ask(services, "/leqv_et KA45")

    query = _press(services, "ri:yes")

    assert query.edited == [expected]


def test_a_role_change_between_prompt_and_press_is_refused(tmp_path: Path) -> None:
    reference_images, _ = _revoker(side_effect=AuthorizationDenied("Operation requires an admin"))
    services = _services(tmp_path, reference_images=reference_images)
    _ask(services, "/leqv_et KA45")

    query = _press(services, "ri:yes")

    assert query.edited == [messages.NO_PERMISSION]


@pytest.mark.parametrize(
    "failure",
    [SQLAlchemyError("database is down"), OSError("disk is full"), ValueError("bad row")],
)
def test_a_failed_revocation_degrades_instead_of_crashing(
    tmp_path: Path, failure: Exception
) -> None:
    reference_images, _ = _revoker(side_effect=failure)
    services = _services(tmp_path, reference_images=reference_images)
    _ask(services, "/leqv_et KA45")

    query = _press(services, "ri:yes")

    assert query.edited == [messages.TEMPORARY_ERROR]


def test_a_revocation_failure_leaves_no_prompt_behind(tmp_path: Path) -> None:
    reference_images, _ = _revoker(side_effect=SQLAlchemyError("database is down"))
    revokes = ReferenceRevokeState()
    services = _services(tmp_path, reference_images=reference_images, revokes=revokes)
    _ask(services, "/leqv_et KA45")

    _press(services, "ri:yes")

    # The claim was already consumed, so a retry has to come from a fresh /leqv_et.
    assert revokes.code_for(ADMIN_TELEGRAM_ID) is None


def test_the_revoke_button_has_its_own_callback_prefix(tmp_path: Path) -> None:
    """A new button must reach its own handler instead of shadowing data deletion or reminders."""

    application = create_telegram_application("test-token", _services(tmp_path))
    revoke_handlers = [
        handler
        for group in application.handlers.values()
        for handler in group
        if getattr(getattr(handler, "pattern", None), "pattern", None) == "^ri:"
    ]

    assert len(revoke_handlers) == 1
