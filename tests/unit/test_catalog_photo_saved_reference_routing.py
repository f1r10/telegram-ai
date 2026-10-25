"""Photo lookup must search PDF catalog even when query bytes were saved earlier."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

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
from office_assistant.integrations.telegram.state import PasswordAttemptLimiter, PasswordEntryState
from office_assistant.repositories.product_image_relations import ProductImageRelationRepository

# These tests exercise a private handler boundary without changing the public API.
# pyright: reportPrivateUsage=false


class FakeSession:
    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def scalar(self, _query: object) -> int:
        # Exact bytes have an ImageAsset, but no confirmed product relation.
        return 5

    async def execute(self, _query: object) -> SimpleNamespace:
        def no_rows() -> list[object]:
            return []

        return SimpleNamespace(all=no_rows)


@pytest.mark.parametrize("pdf_has_result", [True, False])
def test_previously_saved_unapproved_photo_still_queries_pdf_catalog(
    monkeypatch: object, pdf_has_result: bool
) -> None:
    from _pytest.monkeypatch import MonkeyPatch

    user = User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)
    repo = SimpleNamespace(active_confirmation_holder_by_content=AsyncMock(return_value=None))
    mock = cast(MonkeyPatch, monkeypatch)

    def make_repo(_session: AsyncSession) -> ProductImageRelationRepository:
        return cast(ProductImageRelationRepository, repo)

    mock.setattr(handlers, "ProductImageRelationRepository", make_repo)
    catalog = AsyncMock(return_value=pdf_has_result)
    mock.setattr(handlers, "_reply_pdf_catalog_candidates", catalog)
    upload = AsyncMock()
    mock.setattr(handlers, "_handle_upload", upload)
    services = TelegramServices(
        authentication=cast(AuthenticationService, object()),
        users=cast(
            UserManagementService,
            SimpleNamespace(get_by_telegram_id=AsyncMock(return_value=user)),
        ),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        session_factory=cast(async_sessionmaker[AsyncSession], lambda: FakeSession()),
        storage=cast(LocalFileStorage, SimpleNamespace(root=Path("/tmp"))),
    )
    telegram_file = SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=bytearray(b"saved-image-bytes"))
    )
    context = cast(
        ContextTypes.DEFAULT_TYPE,
        SimpleNamespace(
            bot_data={"phase2_services": services},
            user_data={},
            bot=SimpleNamespace(get_file=AsyncMock(return_value=telegram_file)),
        ),
    )
    message = SimpleNamespace(
        photo=[SimpleNamespace(file_id="file-id", file_size=100)],
        caption="Şəkli yoxla",
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
    asyncio.run(handlers.handle_photo(update, context))
    catalog.assert_awaited_once()
    upload.assert_not_awaited()
    if pdf_has_result:
        message.reply_text.assert_not_awaited()
    else:
        answer = message.reply_text.await_args.args[0]
        assert "məhsul koduna" in answer
        assert "Yadda saxlamaq" in answer
