"""Telegram sales confirmation is authenticated, explicit and replay-safe."""

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
from office_assistant.application.ingestion import IngestionSessionService, SessionSummary
from office_assistant.application.sales_ledger import SalesLedgerService
from office_assistant.application.user_management import UserManagementService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.integrations.telegram.handlers import (
    SALES_DRAFT_KEY,  # pyright: ignore[reportPrivateUsage]
    TelegramServices,
    _try_sales_turn,  # pyright: ignore[reportPrivateUsage]
    handle_sales_callback,
    handle_save,
)
from office_assistant.integrations.telegram.state import PasswordAttemptLimiter, PasswordEntryState


def _context(role: UserRole = UserRole.ADMIN) -> tuple[SimpleNamespace, User]:
    user = User(id=1, telegram_user_id=123, role=role, status=UserStatus.ACTIVE)
    ledger = SimpleNamespace(
        record=AsyncMock(
            return_value=SimpleNamespace(
                invoice_number="INV-1",
                customer_name="A Şirkəti",
                amount=Decimal("123.45"),
                currency="AZN",
            )
        ),
        totals=AsyncMock(),
    )
    services = TelegramServices(
        authentication=cast(AuthenticationService, object()),
        users=cast(
            UserManagementService,
            SimpleNamespace(get_by_telegram_id=AsyncMock(return_value=user)),
        ),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        sales=cast(SalesLedgerService, ledger),
    )
    context = SimpleNamespace(bot_data={"phase2_services": services}, user_data={})
    return context, user


def _update(message: SimpleNamespace, query: SimpleNamespace | None = None) -> Update:
    return cast(
        Update,
        SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE, id=123),
            effective_user=TelegramUser(123, "Tester", False),
            callback_query=query,
        ),
    )


def _message(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, chat_id=123, message_id=11, reply_text=AsyncMock())


def test_post_sale_requires_second_click_and_is_not_replayable() -> None:
    context, user = _context()
    services = cast(TelegramServices, context.bot_data["phase2_services"])
    message = _message("Satış əlavə et: A Şirkəti | 2026-10-01 | INV-1 | 123.45 | AZN")
    accepted = asyncio.run(
        _try_sales_turn(
            _update(message),
            cast(ContextTypes.DEFAULT_TYPE, context),
            services,
            user,
            message.text,
        )
    )
    assert accepted
    assert SALES_DRAFT_KEY in context.user_data
    services.sales.record.assert_not_awaited()  # type: ignore[union-attr]
    token = context.user_data[SALES_DRAFT_KEY][0]
    query = SimpleNamespace(
        data=f"sl:ok:{token}", answer=AsyncMock(), edit_message_text=AsyncMock()
    )
    asyncio.run(
        handle_sales_callback(_update(message, query), cast(ContextTypes.DEFAULT_TYPE, context))
    )
    assert SALES_DRAFT_KEY not in context.user_data
    services.sales.record.assert_awaited_once()  # type: ignore[union-attr]
    assert "SATIŞ QEYD EDİLDİ" in query.edit_message_text.await_args.args[0]
    another_query = SimpleNamespace(
        data=f"sl:ok:{token}", answer=AsyncMock(), edit_message_text=AsyncMock()
    )
    asyncio.run(
        handle_sales_callback(
            _update(message, another_query), cast(ContextTypes.DEFAULT_TYPE, context)
        )
    )
    assert "etibarlı deyil" in another_query.edit_message_text.await_args.args[0]
    services.sales.record.assert_awaited_once()  # type: ignore[union-attr]


def test_non_admin_cannot_initiate_ledger_write() -> None:
    context, user = _context(UserRole.USER)
    services = cast(TelegramServices, context.bot_data["phase2_services"])
    message = _message("Satış əlavə et: A Şirkəti | 2026-10-01 | INV-1 | 123.45 | AZN")
    accepted = asyncio.run(
        _try_sales_turn(
            _update(message),
            cast(ContextTypes.DEFAULT_TYPE, context),
            services,
            user,
            message.text,
        )
    )
    assert accepted
    assert SALES_DRAFT_KEY not in context.user_data
    services.sales.record.assert_not_awaited()  # type: ignore[union-attr]
    assert "administrator" in message.reply_text.await_args.args[0]


def test_save_callback_does_not_ingest_bot_authored_menu_copy() -> None:
    context, _user = _context()
    services = cast(TelegramServices, context.bot_data["phase2_services"])
    ingestion = SimpleNamespace(
        add_instruction=AsyncMock(),
        commit=AsyncMock(
            return_value=SessionSummary(
                session_id=1,
                file_count=1,
                instruction_count=0,
                duplicate_count=0,
                job_id=10,
            )
        ),
    )
    services.ingestion = cast(IngestionSessionService, ingestion)
    message = _message("📄 SƏNƏD ƏLAVƏ ET\nİndi fayl göndərin")
    query = SimpleNamespace(data="om:save")
    asyncio.run(handle_save(_update(message, query), cast(ContextTypes.DEFAULT_TYPE, context)))
    ingestion.add_instruction.assert_not_awaited()
    ingestion.commit.assert_awaited_once()
