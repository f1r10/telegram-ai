"""Telegram stock turns are authorized, confirmed and distinct from RAG."""

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from telegram import Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.inventory import (
    InventoryLedgerService,
    StockEntry,
    StockLocation,
    StockSnapshot,
)
from office_assistant.application.user_management import UserManagementService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.integrations.telegram.handlers import (
    STOCK_DRAFT_KEY,
    TelegramServices,
    handle_password_text,
    handle_stock_callback,
    handle_stock_command,
)
from office_assistant.integrations.telegram.state import PasswordAttemptLimiter, PasswordEntryState


class FakeUsers:
    def __init__(self, user: User) -> None:
        self.user = user

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        return self.user if telegram_user_id == self.user.telegram_user_id else None


def _context(*, admin: bool = False) -> tuple[SimpleNamespace, SimpleNamespace]:
    user = User(
        id=1,
        telegram_user_id=123,
        role=UserRole.ADMIN if admin else UserRole.USER,
        status=UserStatus.ACTIVE,
    )
    stock = SimpleNamespace(
        snapshot=AsyncMock(return_value=StockSnapshot("KA45", (), None)),
        record=AsyncMock(
            return_value=StockEntry(
                "KA45", "Əsas", "OPENING", Decimal("10"), Decimal("10"), "SAY-001"
            )
        ),
    )
    services = TelegramServices(
        authentication=cast(AuthenticationService, object()),
        users=cast(UserManagementService, FakeUsers(user)),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        inventory=cast(InventoryLedgerService, stock),
    )
    context = SimpleNamespace(
        bot_data={"phase2_services": services},
        user_data={},
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
    )
    return context, stock


def _message(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        chat_id=123,
        message_id=18,
        reply_text=AsyncMock(),
        photo=None,
        document=None,
        reply_to_message=None,
    )


def _update(message: SimpleNamespace, query: SimpleNamespace | None = None) -> Update:
    return cast(
        Update,
        SimpleNamespace(
            effective_user=TelegramUser(123, "Tester", False),
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE, id=123),
            effective_message=message,
            callback_query=query,
            update_id=18,
        ),
    )


def test_missing_opening_is_reported_unknown_not_zero() -> None:
    context, inventory = _context()
    message = _message("Stok: KA45")
    asyncio.run(handle_password_text(_update(message), cast(ContextTypes.DEFAULT_TYPE, context)))
    inventory.snapshot.assert_awaited_once()
    inventory.record.assert_not_awaited()
    response = message.reply_text.await_args.args[0]
    assert "MƏLUM DEYİL" in response
    assert "0.000" not in response


def test_known_stock_splits_warehouses_without_an_invented_total() -> None:
    context, inventory = _context()
    inventory.snapshot.return_value = StockSnapshot(
        "KA45",
        (StockLocation("Əsas", Decimal("10.5")), StockLocation("Kənar", Decimal("2"))),
        Decimal("12.5"),
    )
    message = _message("/stok KA45")
    asyncio.run(handle_stock_command(_update(message), cast(ContextTypes.DEFAULT_TYPE, context)))
    result = message.reply_text.await_args.args[0]
    assert "12.500" in result
    assert "Əsas: 10.500" in result
    assert "Kənar: 2.000" in result


def test_non_admin_cannot_start_stock_confirmation() -> None:
    context, inventory = _context(admin=False)
    message = _message("Anbar əməliyyatı: KA45 | Əsas | AÇILIŞ | 10 | SAY-001")
    asyncio.run(handle_password_text(_update(message), cast(ContextTypes.DEFAULT_TYPE, context)))
    assert STOCK_DRAFT_KEY not in context.user_data
    inventory.record.assert_not_awaited()
    assert "administrator" in message.reply_text.await_args.args[0]


def test_admin_approval_is_one_shot_and_cancelled_action_is_readonly() -> None:
    context, inventory = _context(admin=True)
    message = _message("Anbar əməliyyatı: KA45 | Əsas | AÇILIŞ | 10 | SAY-001")
    asyncio.run(handle_password_text(_update(message), cast(ContextTypes.DEFAULT_TYPE, context)))
    assert STOCK_DRAFT_KEY in context.user_data
    keyboard = message.reply_text.await_args.kwargs["reply_markup"]
    button = keyboard.inline_keyboard[0][0]
    query = SimpleNamespace(
        data=button.callback_data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    asyncio.run(
        handle_stock_callback(_update(message, query), cast(ContextTypes.DEFAULT_TYPE, context))
    )
    inventory.record.assert_awaited_once()
    assert STOCK_DRAFT_KEY not in context.user_data
    assert "TƏSDİQLƏNDİ" in query.edit_message_text.await_args.args[0]
    asyncio.run(
        handle_stock_callback(_update(message, query), cast(ContextTypes.DEFAULT_TYPE, context))
    )
    inventory.record.assert_awaited_once()
    assert "etibarlı deyil" in query.edit_message_text.await_args.args[0]

    another = _message("Anbar əməliyyatı: KA45 | Əsas | ÇIXIŞ | 1 | CI-002")
    asyncio.run(handle_password_text(_update(another), cast(ContextTypes.DEFAULT_TYPE, context)))
    keyboard = another.reply_text.await_args.kwargs["reply_markup"]
    cancel_query = SimpleNamespace(
        data=keyboard.inline_keyboard[0][1].callback_data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    asyncio.run(
        handle_stock_callback(
            _update(another, cancel_query), cast(ContextTypes.DEFAULT_TYPE, context)
        )
    )
    inventory.record.assert_awaited_once()
    assert STOCK_DRAFT_KEY not in context.user_data
