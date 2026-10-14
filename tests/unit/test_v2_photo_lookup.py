"""Telegram V2 recognizes photo lookup without silently saving incoming pictures."""

from office_assistant.integrations.telegram.handlers import (
    _is_photo_lookup_request,  # pyright: ignore[reportPrivateUsage]
)


def test_photo_lookup_in_azerbaijani() -> None:
    assert _is_photo_lookup_request("Bu məhsul bizdə varmı?")
    assert _is_photo_lookup_request("Şəkli yoxla")
    assert _is_photo_lookup_request("Bazadan bax, bu bizde var?")
    assert not _is_photo_lookup_request(None)
    assert not _is_photo_lookup_request("Bu KA45-dir, yadda saxla")
    assert not _is_photo_lookup_request("Bu məhsulun yeni nümunəsidir")


def test_v2_exact_photo_match_does_not_upload(monkeypatch: object) -> None:
    import asyncio
    from pathlib import Path
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import pytest
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from telegram import Update
    from telegram import User as TelegramUser
    from telegram.constants import ChatType
    from telegram.ext import ContextTypes

    from office_assistant.application.authentication import AuthenticationService
    from office_assistant.application.user_management import UserManagementService
    from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
    from office_assistant.infrastructure.storage.local import LocalFileStorage
    from office_assistant.integrations.telegram import handlers
    from office_assistant.integrations.telegram.handlers import TelegramServices
    from office_assistant.integrations.telegram.state import (
        PasswordAttemptLimiter,
        PasswordEntryState,
    )

    class Session:
        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    user = User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)
    users = SimpleNamespace(get_by_telegram_id=AsyncMock(return_value=user))
    repo = SimpleNamespace(
        active_confirmation_holder_by_content=AsyncMock(return_value=(uuid4(), "KA45"))
    )
    from office_assistant.repositories.product_image_relations import ProductImageRelationRepository

    def make_repo(_session: AsyncSession) -> ProductImageRelationRepository:
        return cast(ProductImageRelationRepository, repo)

    cast(pytest.MonkeyPatch, monkeypatch).setattr(
        handlers, "ProductImageRelationRepository", make_repo
    )
    upload = AsyncMock()
    cast(pytest.MonkeyPatch, monkeypatch).setattr(handlers, "_handle_upload", upload)
    services = TelegramServices(
        authentication=cast(AuthenticationService, object()),
        users=cast(UserManagementService, users),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        session_factory=cast(async_sessionmaker[AsyncSession], lambda: Session()),
        storage=cast(LocalFileStorage, SimpleNamespace(root=Path("/tmp"))),
    )
    telegram_file = SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=bytearray(b"same bytes"))
    )
    context = SimpleNamespace(
        bot_data={"phase2_services": services},
        user_data={},
        bot=SimpleNamespace(get_file=AsyncMock(return_value=telegram_file)),
    )
    message = SimpleNamespace(
        photo=[SimpleNamespace(file_id="photo-id", file_size=10)],
        caption="Bu məhsul bizdə varmı?",
        caption_entities=[],
        reply_text=AsyncMock(),
    )
    update = cast(
        Update,
        SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type=ChatType.PRIVATE),
            effective_user=TelegramUser(123, "Tester", False),
        ),
    )
    asyncio.run(handlers.handle_photo(update, cast(ContextTypes.DEFAULT_TYPE, context)))
    upload.assert_not_awaited()
    reply = message.reply_text.await_args.args[0]
    assert "KA45" in reply
    assert "stok" in reply
    assert "MƏHSUL TAPILDI" in reply
    assert repo.active_confirmation_holder_by_content.await_count == 1
