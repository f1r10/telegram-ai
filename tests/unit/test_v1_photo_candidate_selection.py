# pyright: reportPrivateUsage=false
"""A visually similar SKU must be chosen, revalidated and queried read-only."""

import asyncio
from hashlib import sha256
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
from office_assistant.processing.visual_ann import IndexedProduct, IndexedResults


def setup_case(monkeypatch: pytest.MonkeyPatch) -> tuple[object, object, object, object]:
    actor = 123
    flow = add_photo(None, actor, "f-1", 10, 20, None, question="Bunun qiyməti nə qədərdir?")
    verified = IndexedResults((IndexedProduct("KA45", 0.93),), ())
    services = SimpleNamespace(
        users=SimpleNamespace(
            get_by_telegram_id=AsyncMock(
                return_value=cast(User, SimpleNamespace(id=9, status=UserStatus.ACTIVE))
            )
        ),
        indexed_visual=SimpleNamespace(search=AsyncMock(return_value=verified)),
    )

    def service_for(_context: ContextTypes.DEFAULT_TYPE) -> handlers.TelegramServices:
        return cast(handlers.TelegramServices, services)

    def identity_for(_update: Update) -> TelegramIdentity:
        return cast(TelegramIdentity, SimpleNamespace(telegram_user_id=actor))

    def is_private(_update: Update) -> bool:
        return True

    monkeypatch.setattr(handlers, "_services", service_for)
    monkeypatch.setattr(handlers, "_identity", identity_for)
    monkeypatch.setattr(handlers, "_is_private", is_private)
    monkeypatch.setattr(handlers, "_photo_download", AsyncMock(return_value=b"image"))

    message = SimpleNamespace(reply_text=AsyncMock())
    context = SimpleNamespace(
        user_data={PHOTO_FLOW_KEY: flow},
        bot=SimpleNamespace(send_message=AsyncMock()),
    )
    return flow, services, message, context


def test_ranked_page_offers_question_action_only_for_user_photo_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow, services, message, context = setup_case(monkeypatch)
    del services
    ranked = IndexedResults((IndexedProduct("KA45", 0.93),), ())
    asyncio.run(
        handlers._send_indexed_visual_page(
            cast(object, message), cast(ContextTypes.DEFAULT_TYPE, context), 123, ranked
        )
    )
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert any(callback.startswith(f"pi:{flow.token}:ask:0:0:0:") for callback in callbacks)
    flow.photos[0] = handlers.TelegramPhotoRef("f-1", 10, 20, None)
    asyncio.run(
        handlers._send_indexed_visual_page(
            cast(object, message), cast(ContextTypes.DEFAULT_TYPE, context), 123, ranked
        )
    )
    markup = message.reply_text.await_args.kwargs["reply_markup"]
    assert not any(
        button.callback_data and ":ask:" in button.callback_data
        for row in markup.inline_keyboard
        for button in row
    )


def test_candidate_answer_requires_current_rank_and_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow, services, message, context = setup_case(monkeypatch)
    answer = AsyncMock(return_value="Namizəd KA45 barədə mənbəli məlumat")
    monkeypatch.setattr(handlers, "_confirmed_photo_question_answer", answer)
    digest = sha256(b"KA45").hexdigest()[:12]
    query = SimpleNamespace(
        data=f"pi:{flow.token}:ask:0:0:0:{digest}",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = cast(Update, SimpleNamespace(callback_query=query, effective_message=message))
    asyncio.run(
        handlers.handle_photo_choice_callback(
            update, cast(ContextTypes.DEFAULT_TYPE, context)
        )
    )
    answer.assert_awaited_once()
    assert answer.await_args.kwargs == {"exact_photo": False}
    context.bot.send_message.assert_awaited_with(
        chat_id=123, text="Namizəd KA45 barədə mənbəli məlumat"
    )
    services.indexed_visual.search.assert_awaited_once_with(b"image")

    answer.reset_mock()
    context.bot.send_message.reset_mock()
    query.data = f"pi:{flow.token}:ask:0:0:0:{'a' * 12}"
    asyncio.run(
        handlers.handle_photo_choice_callback(
            update, cast(ContextTypes.DEFAULT_TYPE, context)
        )
    )
    answer.assert_not_awaited()
    assert "təsdiqlənmədi" in context.bot.send_message.await_args.kwargs["text"]


def test_selected_candidate_answer_never_claims_exact_photo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del monkeypatch
    actor = cast(User, SimpleNamespace(id=5))
    cited = SimpleNamespace(
        answer="Bu kod üçün yalnız mənbəli məlumatdır [1].",
        citations=(SimpleNamespace(number=1, label="Mənbə: aktiv kataloq"),),
    )
    service = cast(
        handlers.TelegramServices,
        SimpleNamespace(internal_evidence=SimpleNamespace(answer=AsyncMock(return_value=cited))),
    )
    result = asyncio.run(
        handlers._confirmed_photo_question_answer(
            service, actor, "Bu məhsulun qiyməti nədir?", "KA45", exact_photo=False
        )
    )
    assert "SİZİN SEÇDİYİNİZ VİZUAL NAMİZƏD" in result
    assert "eyni şəkil faylı təsdiqlənib" not in result
    assert "yalnız sizin seçdiyiniz" in result
    assert "Mənbələr:" in result
