"""A user photo question survives the choice menu and uses only confirmed facts."""

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from office_assistant.infrastructure.database.models import User, UserStatus
from office_assistant.integrations.telegram import handlers
from office_assistant.integrations.telegram.photo_flow import PHOTO_FLOW_KEY, add_photo


def test_album_keeps_per_photo_question_without_cross_actor_reuse() -> None:
    first = add_photo(
        None, 123, "front", 21, 31, "group-1", question="Bunun qiyməti nə qədərdir?"
    )
    second = add_photo(
        first, 123, "back", 22, 32, "group-1", question="Neçə ədəd satılıb?"
    )
    assert first is second
    assert [item.question for item in second.photos] == [
        "Bunun qiyməti nə qədərdir?",
        "Neçə ədəd satılıb?",
    ]
    another = add_photo(second, 456, "different", 23, 33, "group-1")
    assert len(another.photos) == 1
    assert another.photos[0].question is None
    assert another.token != second.token


def test_only_real_photo_questions_are_sent_as_verified_product_queries() -> None:
    scoped = handlers._confirmed_photo_question("Bu məhsulun qiyməti neçədir?", "KA45")
    assert scoped is not None
    assert "KA45" in scoped
    assert "qiyməti" in scoped
    assert handlers._confirmed_photo_question("Qara metal qol saatı", "KA45") is None
    assert handlers._confirmed_photo_question(None, "KA45") is None


def test_confirmed_photo_answer_requires_cited_evidence() -> None:
    answered = AsyncMock(
        return_value=SimpleNamespace(
            answer="Kataloqdakı təsdiqli qiymət: 50 AZN [1].",
            citations=(SimpleNamespace(number=1, label="Kataloq məhsul məlumatı"),),
        )
    )
    service = cast(
        handlers.TelegramServices,
        SimpleNamespace(internal_evidence=SimpleNamespace(answer=answered)),
    )
    actor = cast(User, SimpleNamespace(id=5))
    response = asyncio.run(
        handlers._confirmed_photo_question_answer(
            service, actor, "Bu məhsulun qiyməti nə qədərdir?", "KA45"
        )
    )
    assert "Kod: KA45" in response
    assert "50 AZN" in response
    assert "Mənbələr:" in response
    assert answered.await_args.kwargs == {"force_internal": True}
    assert "KA45" in answered.await_args.args[1]


def test_uncited_or_failing_evidence_cannot_invent_price() -> None:
    actor = cast(User, SimpleNamespace(id=5))
    no_citations = AsyncMock(
        return_value=SimpleNamespace(answer="Qiymət 500 AZN", citations=())
    )
    service = cast(
        handlers.TelegramServices,
        SimpleNamespace(internal_evidence=SimpleNamespace(answer=no_citations)),
    )
    response = asyncio.run(
        handlers._confirmed_photo_question_answer(service, actor, "Qiyməti nədir?", "KA45")
    )
    assert "500 AZN" not in response
    assert "mənbəli fakt tapılmadı" in response
    failed = cast(
        handlers.TelegramServices,
        SimpleNamespace(internal_evidence=SimpleNamespace(answer=AsyncMock(side_effect=OSError))),
    )
    response = asyncio.run(
        handlers._confirmed_photo_question_answer(failed, actor, "Stok neçədir?", "KA45")
    )
    assert "Kod: KA45" in response
    assert "mənbəli cavabı hazırda əldə edilmədi" in response


def test_db_choice_passes_original_photo_question_not_bot_menu_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = 123
    flow = add_photo(
        None, actor, "f-1", 10, 20, None, question="Bu məhsulun qiyməti nədir?"
    )
    query = SimpleNamespace(
        data=f"pi:{flow.token}:db",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    user = cast(User, SimpleNamespace(id=9, status=UserStatus.ACTIVE))
    services = SimpleNamespace(
        users=SimpleNamespace(get_by_telegram_id=AsyncMock(return_value=user))
    )
    search = AsyncMock()
    monkeypatch.setattr(handlers, "_services", lambda _context: services)
    monkeypatch.setattr(
        handlers, "_identity", lambda _update: SimpleNamespace(telegram_user_id=actor)
    )
    monkeypatch.setattr(handlers, "_is_private", lambda _update: True)
    monkeypatch.setattr(handlers, "_photo_download", AsyncMock(return_value=b"image"))
    monkeypatch.setattr(handlers, "_handle_exact_photo_lookup", search)
    update = cast(
        Update,
        SimpleNamespace(callback_query=query, effective_message=SimpleNamespace(caption=None)),
    )
    context = cast(
        ContextTypes.DEFAULT_TYPE,
        SimpleNamespace(
            user_data={PHOTO_FLOW_KEY: flow}, bot=SimpleNamespace(send_message=AsyncMock())
        ),
    )
    asyncio.run(handlers.handle_photo_choice_callback(update, context))
    search.assert_awaited_once_with(
        update, context, image_bytes=b"image", photo_index=0, question="Bu məhsulun qiyməti nədir?"
    )
