"""A source button never leaks a later answer's original document."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from telegram import Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.user_management import UserManagementService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.handlers import (
    SOURCE_BUTTONS_KEY,
    TelegramServices,
    _answer_markup,  # pyright: ignore[reportPrivateUsage]
    handle_source_button,
)
from office_assistant.integrations.telegram.state import PasswordAttemptLimiter, PasswordEntryState
from office_assistant.internal_evidence.delivery import DeliverableFile, SourceFileDeliveryService
from office_assistant.internal_evidence.models import (
    InternalAnswer,
    RoutingCategory,
    UnifiedCitation,
)


class FakeUsers:
    async def get_by_telegram_id(self, user_id: int) -> User | None:
        if user_id != 123:
            return None
        return User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)


class FakeDelivery:
    def __init__(self, file: DeliverableFile) -> None:
        self.file = file
        self.resolve_recent = AsyncMock(return_value=file)


def _context(path: Path) -> tuple[SimpleNamespace, FakeDelivery]:
    service = FakeDelivery(DeliverableFile(path, "source.pdf", "application/pdf", 15))
    services = TelegramServices(
        authentication=cast(AuthenticationService, object()),
        users=cast(UserManagementService, FakeUsers()),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        file_delivery=cast(SourceFileDeliveryService, service),
    )
    bot = SimpleNamespace(
        send_message=AsyncMock(),
        send_document=AsyncMock(),
    )
    return SimpleNamespace(
        bot_data={"phase2_services": services},
        user_data={},
        bot=bot,
    ), service


def _answer(number: int | None = 1) -> InternalAnswer:
    sources = (
        (
            UnifiedCitation(
                evidence_id=f"file:{number}",
                number=number,
                source_class="INTERNAL_DOCUMENT",
                filename=f"paper-{number}.pdf",
                file_id=15,
                label="original document",
            ),
        )
        if number is not None
        else ()
    )
    return InternalAnswer("Grounded answer", RoutingCategory.INTERNAL_DOCUMENT, sources)


def _query(value: str) -> SimpleNamespace:
    return SimpleNamespace(data=value, answer=AsyncMock(), edit_message_text=AsyncMock())


def _update(query: SimpleNamespace, *, private: bool = True) -> Update:
    return cast(
        Update,
        SimpleNamespace(
            effective_user=TelegramUser(123, "Employee", False),
            effective_chat=SimpleNamespace(
                type=ChatType.PRIVATE if private else ChatType.GROUP, id=123
            ),
            callback_query=query,
        ),
    )


def test_source_is_downloaded_directly_with_consent_and_existing_authorization(
    tmp_path: Path,
) -> None:
    path = tmp_path / "original.pdf"
    path.write_bytes(b"not-secret-fixture-bytes")
    context, service = _context(path)
    services = context.bot_data["phase2_services"]
    keyboard = _answer_markup(cast(ContextTypes.DEFAULT_TYPE, context), services, _answer())
    buttons = keyboard.inline_keyboard[0]
    assert [button.text for button in buttons] == ["📎 Mənbə 1"]
    query = _query(buttons[0].callback_data or "")
    asyncio.run(handle_source_button(_update(query), cast(ContextTypes.DEFAULT_TYPE, context)))
    query.edit_message_text.assert_not_awaited()
    query.answer.assert_awaited_once()
    service.resolve_recent.assert_awaited_once_with(123, 1)
    context.bot.send_document.assert_awaited_once()
    assert context.bot.send_document.await_args.kwargs["filename"] == "source.pdf"
    context.bot.send_message.assert_not_awaited()


def test_source_token_rejects_stale_index_instead_of_delivering_wrong_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "original.pdf"
    path.write_bytes(b"fixture")
    context, service = _context(path)
    services = context.bot_data["phase2_services"]
    first = _answer_markup(cast(ContextTypes.DEFAULT_TYPE, context), services, _answer(1))
    first_button = first.inline_keyboard[0][0]
    _answer_markup(cast(ContextTypes.DEFAULT_TYPE, context), services, _answer(2))
    query = _query(first_button.callback_data or "")
    asyncio.run(handle_source_button(_update(query), cast(ContextTypes.DEFAULT_TYPE, context)))
    service.resolve_recent.assert_not_awaited()
    context.bot.send_document.assert_not_awaited()
    assert context.bot.send_message.await_args.kwargs["text"] == messages.MENU_SOURCE_BUTTON_EXPIRED
    assert SOURCE_BUTTONS_KEY in context.user_data
    _answer_markup(cast(ContextTypes.DEFAULT_TYPE, context), services, _answer(None))
    assert SOURCE_BUTTONS_KEY not in context.user_data


def test_source_index_tampering_and_expired_button_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"fixture")
    context, service = _context(path)
    services = context.bot_data["phase2_services"]
    keyboard = _answer_markup(cast(ContextTypes.DEFAULT_TYPE, context), services, _answer())
    callback = keyboard.inline_keyboard[0][0].callback_data or ""
    forged = _query(callback.rsplit(":", 1)[0] + ":2")
    asyncio.run(handle_source_button(_update(forged), cast(ContextTypes.DEFAULT_TYPE, context)))
    token, _expiry, indices = context.user_data[SOURCE_BUTTONS_KEY]
    context.user_data[SOURCE_BUTTONS_KEY] = (token, -1.0, indices)
    asyncio.run(
        handle_source_button(_update(_query(callback)), cast(ContextTypes.DEFAULT_TYPE, context))
    )
    service.resolve_recent.assert_not_awaited()
    assert context.bot.send_message.await_count == 2


def test_newer_message_cannot_swap_the_button_target_file(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"fixture")
    context, service = _context(path)
    services = context.bot_data["phase2_services"]
    keyboard = _answer_markup(cast(ContextTypes.DEFAULT_TYPE, context), services, _answer())
    callback = keyboard.inline_keyboard[0][0].callback_data or ""
    service.resolve_recent.return_value = DeliverableFile(
        path, "different.pdf", "application/pdf", 99
    )
    asyncio.run(
        handle_source_button(_update(_query(callback)), cast(ContextTypes.DEFAULT_TYPE, context))
    )
    service.resolve_recent.assert_awaited_once_with(123, 1)
    context.bot.send_document.assert_not_awaited()
    assert context.bot.send_message.await_args.kwargs["text"] == messages.MENU_SOURCE_BUTTON_EXPIRED


def test_group_chat_button_never_exposes_a_document(tmp_path: Path) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"fixture")
    context, service = _context(path)
    services = context.bot_data["phase2_services"]
    keyboard = _answer_markup(cast(ContextTypes.DEFAULT_TYPE, context), services, _answer())
    query = _query(keyboard.inline_keyboard[0][0].callback_data or "")
    asyncio.run(
        handle_source_button(
            _update(query, private=False), cast(ContextTypes.DEFAULT_TYPE, context)
        )
    )
    service.resolve_recent.assert_not_awaited()
    context.bot.send_document.assert_not_awaited()
