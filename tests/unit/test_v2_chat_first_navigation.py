"""Nontechnical Telegram chat-first navigation retains answers and company data scopes."""

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.user_management import UserManagementService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.handlers import (
    MENU_SELECTION_KEY,  # pyright: ignore[reportPrivateUsage]
    TelegramServices,
    _reply_long,  # pyright: ignore[reportPrivateUsage]
    handle_answer_navigation,
    handle_menu_callback,
)
from office_assistant.integrations.telegram.state import PasswordAttemptLimiter, PasswordEntryState


class FakeUsers:
    def __init__(self, actor: User | None) -> None:
        self.actor = actor

    async def get_by_telegram_id(self, user_id: int) -> User | None:
        if self.actor is not None and self.actor.telegram_user_id == user_id:
            return self.actor
        return None


def _context(status: UserStatus = UserStatus.ACTIVE) -> SimpleNamespace:
    actor = User(id=1, telegram_user_id=123, status=status, role=UserRole.USER)
    services = TelegramServices(
        authentication=cast(AuthenticationService, object()),
        users=cast(UserManagementService, FakeUsers(actor)),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
    )
    return SimpleNamespace(
        bot_data={"phase2_services": services},
        user_data={},
        bot=SimpleNamespace(send_message=AsyncMock()),
    )


def _update(action: str, query: SimpleNamespace, *, group: bool = False) -> Update:
    return cast(
        Update,
        SimpleNamespace(
            effective_user=TelegramUser(123, "Employee", False),
            effective_chat=SimpleNamespace(
                type=ChatType.GROUP if group else ChatType.PRIVATE,
                id=123,
            ),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
            callback_query=query,
        ),
    )


def _query(action: str) -> SimpleNamespace:
    return SimpleNamespace(
        data=action,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )


def test_baza_from_answer_preserves_original_message_and_scopes_one_turn() -> None:
    context = _context()
    query = _query("nav:baza")
    context.user_data[MENU_SELECTION_KEY] = ("internet", 1e9)
    asyncio.run(
        handle_answer_navigation(
            _update("nav:baza", query), cast(ContextTypes.DEFAULT_TYPE, context)
        )
    )
    query.answer.assert_awaited_once()
    query.edit_message_text.assert_not_awaited()
    message = context.bot.send_message.await_args
    assert message.kwargs["chat_id"] == 123
    assert message.kwargs["text"] == messages.MENU_ASK_INTERNAL
    assert message.kwargs["reply_markup"] is not None
    assert context.user_data[MENU_SELECTION_KEY][0] == "baza"


def test_answer_chat_and_home_clear_stale_internet_mode() -> None:
    for action in ("nav:ask", "nav:home"):
        context = _context()
        context.user_data[MENU_SELECTION_KEY] = ("internet", 1e9)
        query = _query(action)
        asyncio.run(
            handle_answer_navigation(
                _update(action, query), cast(ContextTypes.DEFAULT_TYPE, context)
            )
        )
        assert MENU_SELECTION_KEY not in context.user_data
        assert context.bot.send_message.await_count == 1
        assert context.bot.send_message.await_args.kwargs["text"] == (
            messages.MENU_TITLE if action == "nav:home" else messages.MENU_CHAT_PROMPT
        )
        query.edit_message_text.assert_not_awaited()


def test_answer_buttons_require_active_private_user() -> None:
    disabled = _context(UserStatus.DISABLED)
    query = _query("nav:baza")
    asyncio.run(
        handle_answer_navigation(
            _update("nav:baza", query), cast(ContextTypes.DEFAULT_TYPE, disabled)
        )
    )
    assert disabled.bot.send_message.await_args.kwargs["text"] == messages.NO_PERMISSION
    assert MENU_SELECTION_KEY not in disabled.user_data

    group = _context()
    query = _query("nav:home")
    asyncio.run(
        handle_answer_navigation(
            _update("nav:home", query, group=True),
            cast(ContextTypes.DEFAULT_TYPE, group),
        )
    )
    group.bot.send_message.assert_not_awaited()


def test_help_and_example_callbacks_never_arm_search_or_write() -> None:
    for action, expected in (
        ("om:help", messages.MENU_QUICK_HELP),
        ("om:examples", messages.MENU_EXAMPLES),
    ):
        context = _context()
        context.user_data[MENU_SELECTION_KEY] = ("internet", 1e9)
        query = _query(action)
        asyncio.run(
            handle_menu_callback(_update(action, query), cast(ContextTypes.DEFAULT_TYPE, context))
        )
        assert query.edit_message_text.await_args.args == (expected,)
        keyboard = query.edit_message_text.await_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard
        assert MENU_SELECTION_KEY not in context.user_data


def test_multi_part_answer_has_actions_only_on_last_chunk() -> None:
    message = SimpleNamespace(reply_text=AsyncMock())
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Menyu", callback_data="nav:home")]])
    asyncio.run(_reply_long(message, "a" * 130, 70, reply_markup=markup))
    assert message.reply_text.await_count == 2
    first, second = message.reply_text.await_args_list
    assert first.kwargs == {}
    assert second.kwargs["reply_markup"] == markup
    assert "".join(call.args[0] for call in message.reply_text.await_args_list) == "a" * 130
