"""Small, authenticated, one-turn Telegram menu for non-technical office staff."""

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from telegram import Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from office_assistant.ai.service import InProcessRateGuard
from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.ingestion import IngestionSessionService
from office_assistant.application.user_management import UserManagementService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.handlers import (
    MENU_SELECTION_KEY,  # pyright: ignore[reportPrivateUsage]
    TelegramServices,
    handle_menu,
    handle_menu_callback,
    handle_menu_interruption,
    handle_password_text,
)
from office_assistant.integrations.telegram.state import PasswordAttemptLimiter, PasswordEntryState
from office_assistant.internal_evidence.models import InternalAnswer, RoutingCategory
from office_assistant.web_evidence.models import WebAnswer


class FakeUsers:
    def __init__(self, user: User | None) -> None:
        self.user = user

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        if self.user is None or self.user.telegram_user_id != telegram_user_id:
            return None
        return self.user


def _user(status: UserStatus = UserStatus.ACTIVE, telegram_user_id: int = 123) -> User:
    return User(
        id=telegram_user_id - 122,
        telegram_user_id=telegram_user_id,
        role=UserRole.USER,
        status=status,
    )


def _context(user: User | None) -> tuple[SimpleNamespace, TelegramServices]:
    services = TelegramServices(
        authentication=cast(AuthenticationService, object()),
        users=cast(UserManagementService, FakeUsers(user)),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        rate_guard=InProcessRateGuard(),
    )
    context = SimpleNamespace(
        bot_data={"phase2_services": services},
        user_data={},
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
    )
    return context, services


def _message(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        chat_id=123,
        message_id=17,
        reply_text=AsyncMock(),
        photo=None,
        document=None,
        reply_to_message=None,
    )


def _update(
    message: SimpleNamespace, query: SimpleNamespace | None = None,
    telegram_user_id: int = 123,
) -> Update:
    fake = SimpleNamespace(
        effective_user=TelegramUser(telegram_user_id, "Test", False),
        effective_chat=SimpleNamespace(type=ChatType.PRIVATE, id=123),
        effective_message=message,
        callback_query=query,
    )
    return cast(Update, fake)


def _select(
    context: SimpleNamespace, mode: str, telegram_user_id: int = 123
) -> SimpleNamespace:
    query = SimpleNamespace(
        data=f"om:{mode}",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    asyncio.run(
        handle_menu_callback(
            _update(_message("/menu"), query, telegram_user_id),
            cast(ContextTypes.DEFAULT_TYPE, context),
        )
    )
    return query


def test_active_user_sees_tap_friendly_menu() -> None:
    context, _ = _context(_user())
    message = _message("/menu")
    asyncio.run(handle_menu(_update(message), cast(ContextTypes.DEFAULT_TYPE, context)))
    message.reply_text.assert_awaited_once()
    args = message.reply_text.await_args
    assert args.args == (messages.MENU_TITLE,)
    keyboard = args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in keyboard.inline_keyboard] == [
        "om:baza",
        "om:internet",
        "om:tamaxtar",
        "om:upload",
        "om:help",
    ]


@pytest.mark.parametrize(
    ("user", "expected"),
    [
        (None, messages.UNKNOWN_USER),
        (_user(UserStatus.PENDING), messages.PENDING_STATUS),
        (_user(UserStatus.DISABLED), messages.AUTH_DISABLED),
    ],
)
def test_menu_requires_active_account(user: User | None, expected: str) -> None:
    context, _ = _context(user)
    message = _message("/menu")
    asyncio.run(handle_menu(_update(message), cast(ContextTypes.DEFAULT_TYPE, context)))
    message.reply_text.assert_awaited_once_with(expected)


def test_internet_menu_routes_plain_message_externally_once() -> None:
    user = _user()
    context, services = _context(user)
    web = SimpleNamespace(
        answer_internet=AsyncMock(return_value=WebAnswer("İnternet cavabı.")),
        answer_combined=AsyncMock(),
    )
    internal = SimpleNamespace(answer=AsyncMock(side_effect=AssertionError("internal called")))
    services.web_evidence = cast(object, web)  # type: ignore[assignment]
    services.internal_evidence = cast(object, internal)  # type: ignore[assignment]

    query = _select(context, "internet")
    query.edit_message_text.assert_awaited_once_with(messages.MENU_ASK_INTERNET)
    text = "KA45 modelinin bazar qiyməti neçədir"
    message = _message(text)
    asyncio.run(handle_password_text(_update(message), cast(ContextTypes.DEFAULT_TYPE, context)))
    web.answer_internet.assert_awaited_once_with(user, text)
    internal.answer.assert_not_called()
    assert MENU_SELECTION_KEY not in context.user_data


def test_combined_menu_passes_both_evidence_scopes() -> None:
    user = _user()
    context, services = _context(user)
    web = SimpleNamespace(
        answer_internet=AsyncMock(side_effect=AssertionError("wrong scope")),
        answer_combined=AsyncMock(return_value=WebAnswer("Müqayisə.")),
    )
    services.web_evidence = cast(object, web)  # type: ignore[assignment]
    _select(context, "tamaxtar")
    text = "Bizdə və internetdə KA45 qiyməti"
    asyncio.run(
        handle_password_text(_update(_message(text)), cast(ContextTypes.DEFAULT_TYPE, context))
    )
    web.answer_combined.assert_awaited_once_with(user, text, image_readings=[])
    web.answer_internet.assert_not_called()


def test_internal_menu_keeps_search_inside_company_data() -> None:
    user = _user()
    context, services = _context(user)
    internal = SimpleNamespace(
        answer=AsyncMock(
            return_value=InternalAnswer(
                answer="Yalnız daxili cavab.", route=RoutingCategory.INTERNAL_DOCUMENT
            )
        )
    )
    web = SimpleNamespace(answer_internet=AsyncMock(), answer_combined=AsyncMock())
    services.internal_evidence = cast(object, internal)  # type: ignore[assignment]
    services.web_evidence = cast(object, web)  # type: ignore[assignment]
    _select(context, "baza")
    text = "KA45 stokda varmı"
    asyncio.run(
        handle_password_text(_update(_message(text)), cast(ContextTypes.DEFAULT_TYPE, context))
    )
    internal.answer.assert_awaited_once_with(user, text, force_internal=True, image_readings=[])
    web.answer_internet.assert_not_called()
    web.answer_combined.assert_not_called()


def test_clicking_menu_during_open_upload_session_does_not_change_routing() -> None:
    context, services = _context(_user())
    ingestion = SimpleNamespace(has_open_session=AsyncMock(return_value=True))
    services.ingestion = cast(IngestionSessionService, ingestion)
    query = _select(context, "internet")
    query.edit_message_text.assert_awaited_once_with(messages.MENU_ACTIVE_SESSION)
    assert MENU_SELECTION_KEY not in context.user_data


def test_upload_guide_never_creates_or_saves_an_ingestion_session() -> None:
    context, services = _context(_user())
    query = _select(context, "upload")
    query.edit_message_text.assert_awaited_once_with(messages.MENU_UPLOAD_GUIDE)
    assert MENU_SELECTION_KEY not in context.user_data
    assert services.ingestion is None


def test_expired_menu_selection_must_not_trigger_external_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from office_assistant.integrations.telegram import handlers

    context, services = _context(_user())
    web = SimpleNamespace(answer_internet=AsyncMock())
    services.web_evidence = cast(object, web)  # type: ignore[assignment]
    monkeypatch.setattr(handlers, "monotonic", lambda: 100.0)
    _select(context, "internet")
    monkeypatch.setattr(handlers, "monotonic", lambda: 1000.0)
    message = _message("Bu məhsulu internetdə axtar")
    asyncio.run(handle_password_text(_update(message), cast(ContextTypes.DEFAULT_TYPE, context)))
    message.reply_text.assert_awaited_once_with(messages.MENU_SESSION_EXPIRED)
    web.answer_internet.assert_not_called()
    assert MENU_SELECTION_KEY not in context.user_data


def test_pending_user_cannot_set_web_search_mode_through_stale_callback() -> None:
    context, _ = _context(_user(UserStatus.PENDING))
    query = _select(context, "internet")
    query.edit_message_text.assert_awaited_once_with(messages.PENDING_STATUS)
    assert MENU_SELECTION_KEY not in context.user_data


def test_unsupported_callback_value_never_selects_a_scope() -> None:
    context, _ = _context(_user())
    query = _select(context, "delete_all")
    query.answer.assert_awaited_once()
    query.edit_message_text.assert_not_awaited()
    assert MENU_SELECTION_KEY not in context.user_data



@pytest.mark.parametrize(
    ("later_action", "expected"),
    [
        ("upload", messages.MENU_UPLOAD_GUIDE),
        ("help", messages.HELP_TEXT),
    ],
)
def test_navigation_away_from_internet_releases_armed_scope(
    later_action: str, expected: str
) -> None:
    context, services = _context(_user())
    web = SimpleNamespace(answer_internet=AsyncMock())
    services.web_evidence = cast(object, web)  # type: ignore[assignment]

    _select(context, "internet")
    assert MENU_SELECTION_KEY in context.user_data
    new_selection = _select(context, later_action)
    new_selection.edit_message_text.assert_awaited_once_with(expected)
    assert MENU_SELECTION_KEY not in context.user_data
    web.answer_internet.assert_not_awaited()


def test_reopening_menu_discards_an_older_internet_scope() -> None:
    context, services = _context(_user())
    _select(context, "internet")
    message = _message("/menu")
    asyncio.run(handle_menu(_update(message), cast(ContextTypes.DEFAULT_TYPE, context)))
    assert MENU_SELECTION_KEY not in context.user_data
    assert services.rate_guard is not None


@pytest.mark.parametrize("interruption", ["/start", "/cancel", "/saxla", "photo", "document"])
def test_other_commands_and_uploads_discard_menu_scope(interruption: str) -> None:
    context, _ = _context(_user())
    _select(context, "internet")
    asyncio.run(
        handle_menu_interruption(
            _update(_message(interruption)), cast(ContextTypes.DEFAULT_TYPE, context)
        )
    )
    assert MENU_SELECTION_KEY not in context.user_data


def test_stale_internet_selection_is_cleared_before_access_denial() -> None:
    context, services = _context(_user())
    _select(context, "internet")
    users = cast(FakeUsers, services.users)
    users.user = _user(UserStatus.PENDING)
    query = _select(context, "baza")
    query.edit_message_text.assert_awaited_once_with(messages.PENDING_STATUS)
    assert MENU_SELECTION_KEY not in context.user_data


def test_four_independent_menu_users_dispatch_concurrently_without_scope_leaks() -> None:
    setup: list[tuple[int, str, SimpleNamespace, TelegramServices]] = []
    modes = ("baza", "internet", "tamaxtar", "baza")
    for telegram_id, mode in zip(range(123, 127), modes, strict=True):
        context, services = _context(_user(telegram_user_id=telegram_id))
        services.internal_evidence = cast(
            object,
            SimpleNamespace(
                answer=AsyncMock(
                    return_value=InternalAnswer(
                        answer="Daxili cavab", route=RoutingCategory.INTERNAL_DOCUMENT
                    )
                )
            ),
        )  # type: ignore[assignment]
        services.web_evidence = cast(
            object,
            SimpleNamespace(
                answer_internet=AsyncMock(return_value=WebAnswer("Xarici cavab")),
                answer_combined=AsyncMock(return_value=WebAnswer("Birləşmiş cavab")),
            ),
        )  # type: ignore[assignment]
        _select(context, mode, telegram_id)
        setup.append((telegram_id, mode, context, services))

    async def run_four() -> None:
        await asyncio.gather(
            *(
                handle_password_text(
                    _update(
                        _message(f"İstifadəçi {telegram_id} sorğusu"),
                        telegram_user_id=telegram_id,
                    ),
                    cast(ContextTypes.DEFAULT_TYPE, context),
                )
                for telegram_id, _mode, context, _services in setup
            )
        )

    asyncio.run(run_four())
    for _telegram_id, mode, context, services in setup:
        assert MENU_SELECTION_KEY not in context.user_data
        internal = cast(AsyncMock, services.internal_evidence.answer)  # type: ignore[union-attr]
        external = cast(AsyncMock, services.web_evidence.answer_internet)  # type: ignore[union-attr]
        combined = cast(AsyncMock, services.web_evidence.answer_combined)  # type: ignore[union-attr]
        assert internal.await_count == (1 if mode == "baza" else 0)
        assert external.await_count == (1 if mode == "internet" else 0)
        assert combined.await_count == (1 if mode == "tamaxtar" else 0)
