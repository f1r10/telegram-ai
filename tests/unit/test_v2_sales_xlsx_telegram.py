"""Telegram workbook import: explicit consent, one-shot callback and no silent writes."""

import asyncio
from datetime import date
from decimal import Decimal
from time import monotonic
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from telegram import Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.sales_ledger import SaleDraft, SalesLedgerService
from office_assistant.application.user_management import UserManagementService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.integrations.telegram.handlers import (
    SALES_IMPORT_KEY,
    TelegramServices,
    _is_sales_import_request,  # pyright: ignore[reportPrivateUsage]
    handle_sales_import_callback,
)
from office_assistant.integrations.telegram.state import PasswordAttemptLimiter, PasswordEntryState


def _actor(role: UserRole = UserRole.ADMIN) -> User:
    return User(id=1, telegram_user_id=123, role=role, status=UserStatus.ACTIVE)


def test_sales_import_only_explicit_request() -> None:
    assert _is_sales_import_request("Satış fakturalarını idxal et")
    assert _is_sales_import_request("Bu Excel satışlarını bazaya yaz")
    assert not _is_sales_import_request(None)
    assert not _is_sales_import_request("Bu şirkətin satışları nə qədər olub?")
    assert not _is_sales_import_request("Excel-i yadda saxla")


def test_confirmation_posts_once_and_replay_is_rejected() -> None:
    actor = _actor()
    users = SimpleNamespace(get_by_telegram_id=AsyncMock(return_value=actor))
    batch = (
        SaleDraft("A Şirkəti", date(2026, 10, 1), "QA-2026-1", Decimal("50"), "AZN"),
    )
    sales = SimpleNamespace(record_many=AsyncMock(return_value=[object()]))
    services = TelegramServices(
        authentication=cast(AuthenticationService, object()),
        users=cast(UserManagementService, users),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        sales=cast(SalesLedgerService, sales),
    )
    query = SimpleNamespace(
        data="si:ok:token",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    context = SimpleNamespace(
        bot_data={"phase2_services": services},
        user_data={SALES_IMPORT_KEY: ("token", monotonic() + 600, batch)},
    )
    update = cast(
        Update,
        SimpleNamespace(
            effective_user=TelegramUser(123, "Test", False),
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
            callback_query=query,
        ),
    )
    asyncio.run(
        handle_sales_import_callback(update, cast(ContextTypes.DEFAULT_TYPE, context))
    )
    sales.record_many.assert_awaited_once_with(actor, batch)
    assert SALES_IMPORT_KEY not in context.user_data
    assert "İDXAL TAMAMLANDI" in query.edit_message_text.await_args.args[0]
    asyncio.run(
        handle_sales_import_callback(update, cast(ContextTypes.DEFAULT_TYPE, context))
    )
    sales.record_many.assert_awaited_once()
    assert "etibarlı deyil" in query.edit_message_text.await_args.args[0]


def test_cancel_never_posts_sales() -> None:
    actor = _actor()
    sales = SimpleNamespace(record_many=AsyncMock())
    services = TelegramServices(
        authentication=cast(AuthenticationService, object()),
        users=cast(
            UserManagementService,
            SimpleNamespace(get_by_telegram_id=AsyncMock(return_value=actor)),
        ),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        sales=cast(SalesLedgerService, sales),
    )
    query = SimpleNamespace(
        data="si:no:t",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    context = SimpleNamespace(
        bot_data={"phase2_services": services},
        user_data={
            SALES_IMPORT_KEY: ("t", monotonic() + 60, (SaleDraft(
                "A Şirkəti", date(2026, 10, 1), "QA-1", Decimal("1"), "AZN"
            ),))
        },
    )
    update = cast(
        Update,
        SimpleNamespace(
            effective_user=TelegramUser(123, "Test", False),
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
            callback_query=query,
        ),
    )
    asyncio.run(handle_sales_import_callback(update, cast(ContextTypes.DEFAULT_TYPE, context)))
    sales.record_many.assert_not_awaited()
