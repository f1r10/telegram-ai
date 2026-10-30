# pyright: reportPrivateUsage=false
"""Photo OCR is a separate user choice; the database lookup does not trigger it."""

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from office_assistant.application.authentication import TelegramIdentity
from office_assistant.infrastructure.database.models import User, UserStatus
from office_assistant.integrations.telegram import handlers
from office_assistant.integrations.telegram.photo_flow import PHOTO_FLOW_KEY, add_photo
from office_assistant.internal_evidence.models import InternalAnswer, RoutingCategory, UnifiedCitation
from office_assistant.processing.vision import ImageTranscription


def test_photo_ocr_button_requires_original_question() -> None:
    flow = add_photo(None, 123, "photo", 11, 31, None)
    keyboard = handlers._photo_action_keyboard(flow)
    assert not any(
        isinstance(button.callback_data, str) and ":ocr:" in button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    )
    with_question = add_photo(None, 123, "photo", 11, 31, None, "Fotoda nə yazılıb?")
    keyboard = handlers._photo_action_keyboard(with_question)
    assert any(
        isinstance(button.callback_data, str)
        and button.callback_data == f"pi:{with_question.token}:ocr:0"
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_photo_ocr_callback_uses_original_caption_and_cites_image_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id = 123
    flow = add_photo(None, actor_id, "file-1", 11, 21, None, "Fotoda nə yazılıb?")
    user = cast(User, SimpleNamespace(id=9, status=UserStatus.ACTIVE))
    reader = AsyncMock(
        return_value=ImageTranscription(
            text="Model KA45",
            description="Qol saatı",
            model="test-vision",
            media_type="image/jpeg",
            byte_size=5,
        )
    )
    evidence = AsyncMock(
        return_value=InternalAnswer(
            answer="Şəkildə KA45 yazısı görünür [1].",
            route=RoutingCategory.INTERNAL_DOCUMENT,
            citations=(
                UnifiedCitation(
                    evidence_id="E1",
                    number=1,
                    source_class="image",
                    label="Qoşulmuş foto",
                ),
            ),
        )
    )
    services = SimpleNamespace(
        users=SimpleNamespace(get_by_telegram_id=AsyncMock(return_value=user)),
        image_reader=SimpleNamespace(read=reader),
        internal_evidence=SimpleNamespace(answer=evidence),
        telegram_message_limit=4096,
    )

    def get_services(_context: ContextTypes.DEFAULT_TYPE) -> handlers.TelegramServices:
        return cast(handlers.TelegramServices, services)

    def get_identity(_update: Update) -> TelegramIdentity:
        return cast(TelegramIdentity, SimpleNamespace(telegram_user_id=actor_id))

    def is_private(_update: Update) -> bool:
        return True

    monkeypatch.setattr(handlers, "_services", get_services)
    monkeypatch.setattr(handlers, "_identity", get_identity)
    monkeypatch.setattr(handlers, "_is_private", is_private)
    download = AsyncMock(return_value=b"image")
    monkeypatch.setattr(handlers, "_photo_download", download)
    query = SimpleNamespace(
        data=f"pi:{flow.token}:ocr:0", answer=AsyncMock(), edit_message_text=AsyncMock()
    )
    update = cast(Update, SimpleNamespace(callback_query=query))
    send = AsyncMock()
    context = cast(
        ContextTypes.DEFAULT_TYPE,
        SimpleNamespace(user_data={PHOTO_FLOW_KEY: flow}, bot=SimpleNamespace(send_message=send)),
    )
    asyncio.run(handlers.handle_photo_choice_callback(update, context))
    download.assert_awaited_once_with(context, "file-1")
    reader.assert_awaited_once_with(image_bytes=b"image", media_type="image/jpeg", user_id=9)
    assert evidence.await_count == 1
    call = evidence.await_args
    assert call is not None
    assert call.args[1] == "Fotoda nə yazılıb?"
    assert call.kwargs["force_internal"] is True
    assert call.kwargs["image_readings"][0].text == "Model KA45"
    output = send.await_args
    assert output is not None
    assert "təsdiqlənmiş məhsul kimliyi" in output.kwargs["text"]
    assert "Şəkildə KA45" in output.kwargs["text"]


def test_invalid_ocr_index_never_contacts_model(monkeypatch: pytest.MonkeyPatch) -> None:
    actor_id = 123
    flow = add_photo(None, actor_id, "file-1", 11, 21, None, "Nə yazılıb?")
    reader = AsyncMock()

    def get_services(_context: ContextTypes.DEFAULT_TYPE) -> handlers.TelegramServices:
        return cast(
            handlers.TelegramServices,
            SimpleNamespace(
                users=SimpleNamespace(
                    get_by_telegram_id=AsyncMock(
                        return_value=cast(
                            User, SimpleNamespace(id=9, status=UserStatus.ACTIVE)
                        )
                    )
                ),
                image_reader=SimpleNamespace(read=reader),
                internal_evidence=SimpleNamespace(answer=AsyncMock()),
            ),
        )

    monkeypatch.setattr(handlers, "_services", get_services)
    monkeypatch.setattr(
        handlers,
        "_identity",
        lambda _update: SimpleNamespace(telegram_user_id=actor_id),
    )
    monkeypatch.setattr(handlers, "_is_private", lambda _update: True)
    query = SimpleNamespace(
        data=f"pi:{flow.token}:ocr:9", answer=AsyncMock(), edit_message_text=AsyncMock()
    )
    update = cast(Update, SimpleNamespace(callback_query=query))
    context = cast(ContextTypes.DEFAULT_TYPE, SimpleNamespace(user_data={PHOTO_FLOW_KEY: flow}))
    asyncio.run(handlers.handle_photo_choice_callback(update, context))
    reader.assert_not_awaited()
    query.edit_message_text.assert_awaited_once()
