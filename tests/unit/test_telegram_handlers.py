from dataclasses import dataclass, field
from typing import cast

from telegram import Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.user_management import UserManagementService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.integrations.telegram.app import create_telegram_application
from office_assistant.integrations.telegram.handlers import (
    TelegramServices,
    handle_start,
)
from office_assistant.integrations.telegram.messages import AUTH_PROMPT, WELCOME
from office_assistant.integrations.telegram.state import PasswordAttemptLimiter, PasswordEntryState


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

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        return self.user

    async def touch_active_user(self, telegram_user_id: int) -> None:
        self.touched = True


def _services(users: FakeUsers) -> TelegramServices:
    return TelegramServices(
        authentication=cast(AuthenticationService, object()),
        users=cast(UserManagementService, users),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
    )


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

    assert sum(len(group) for group in application.handlers.values()) == 9
