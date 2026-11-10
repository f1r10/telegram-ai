# pyright: reportPrivateUsage=false
"""Same-album photos are one product: one prompt, one combined result, one save."""

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from telegram import InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from office_assistant.application.authentication import TelegramIdentity
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.integrations.telegram import handlers, messages
from office_assistant.integrations.telegram.photo_flow import (
    PHOTO_FLOW_KEY,
    add_photo,
)
from office_assistant.processing.document_visual_candidates import DocumentImageSource
from office_assistant.processing.visual_ann import (
    IndexedDocument,
    IndexedProduct,
    IndexedResults,
)
from office_assistant.processing.visual_paging import merge_indexed_results


def _doc_source(occurrence_id: int) -> DocumentImageSource:
    return DocumentImageSource(
        occurrence_id=occurrence_id,
        source_file_id=occurrence_id,
        filename=f"catalog-{occurrence_id}.pdf",
        origin="pdf_embedded",
        locator={"page": 1},
        storage_key=f"key-{occurrence_id}",
        content_sha256="0" * 64,
    )


def test_merge_dedupes_by_code_and_occurrence_keeping_strongest_score() -> None:
    first = IndexedResults(
        (IndexedProduct("KA45", 0.90), IndexedProduct("KB12", 0.95)),
        (IndexedDocument(_doc_source(7), 0.88),),
    )
    second = IndexedResults(
        (IndexedProduct("KA45", 0.97), IndexedProduct("KC99", 0.85)),
        (IndexedDocument(_doc_source(7), 0.91), IndexedDocument(_doc_source(8), 0.86)),
    )
    merged = merge_indexed_results([first, second])
    assert not merged.unavailable
    assert [item.code for item in merged.products] == ["KA45", "KB12", "KC99"]
    assert merged.products[0].score == 0.97
    assert [item.source.occurrence_id for item in merged.documents] == [7, 8]
    assert merged.documents[0].score == 0.91


def test_merge_skips_unavailable_parts_and_reports_all_unavailable() -> None:
    good = IndexedResults((IndexedProduct("KA45", 0.9),), ())
    down = IndexedResults((), (), unavailable=True)
    partial = merge_indexed_results([down, good, down])
    assert not partial.unavailable
    assert [item.code for item in partial.products] == ["KA45"]
    assert merge_indexed_results([down, down]).unavailable
    assert merge_indexed_results([]).unavailable
    empty = merge_indexed_results([IndexedResults((), ())])
    assert not empty.unavailable
    assert empty.products == ()
    assert empty.documents == ()


class _Session:
    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def _private(_update: Update) -> bool:
    return True


def _setup_album(
    monkeypatch: pytest.MonkeyPatch,
    *,
    photos: int = 3,
    holder: tuple[UUID, str] | None = None,
    holder_side_effect: list[tuple[UUID, str] | None] | None = None,
    indexed_visual: object = None,
    download_side_effect: list[object] | BaseException | None = None,
) -> SimpleNamespace:
    actor = 123
    flow = add_photo(None, actor, "f-0", 10, 20, "album-9")
    for i in range(1, photos):
        flow = add_photo(flow, actor, f"f-{i}", 10 + i, 20 + i, "album-9")

    repo = SimpleNamespace(
        active_confirmation_holder_by_content=AsyncMock(
            return_value=holder, side_effect=holder_side_effect
        )
    )

    def make_repo(_session: object) -> object:
        return repo

    services = SimpleNamespace(
        users=SimpleNamespace(
            get_by_telegram_id=AsyncMock(
                return_value=cast(
                    User, SimpleNamespace(id=9, role=UserRole.USER, status=UserStatus.ACTIVE)
                )
            )
        ),
        session_factory=lambda: _Session(),
        indexed_visual=indexed_visual,
    )

    def service_for(_context: ContextTypes.DEFAULT_TYPE) -> handlers.TelegramServices:
        return cast(handlers.TelegramServices, services)

    def identity_for(_update: Update) -> TelegramIdentity:
        return cast(TelegramIdentity, SimpleNamespace(telegram_user_id=actor))

    download = AsyncMock(return_value=b"image-bytes", side_effect=download_side_effect)
    monkeypatch.setattr(handlers, "_services", service_for)
    monkeypatch.setattr(handlers, "_identity", identity_for)
    monkeypatch.setattr(handlers, "_is_private", _private)
    monkeypatch.setattr(handlers, "_photo_download", download)
    monkeypatch.setattr(handlers, "ProductImageRelationRepository", make_repo)

    message = SimpleNamespace(reply_text=AsyncMock(return_value=SimpleNamespace(message_id=777)))
    query = SimpleNamespace(
        data=f"pi:{flow.token}:db",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    context = SimpleNamespace(
        user_data={PHOTO_FLOW_KEY: flow},
        bot=SimpleNamespace(send_message=AsyncMock(), get_file=AsyncMock()),
    )
    update = cast(Update, SimpleNamespace(callback_query=query, effective_message=message))
    return SimpleNamespace(
        flow=flow,
        services=services,
        repo=repo,
        message=message,
        query=query,
        context=context,
        update=update,
        download=download,
    )


def _run_db(ns: SimpleNamespace) -> None:
    asyncio.run(
        handlers._handle_album_db_lookup(
            ns.update,
            cast(ContextTypes.DEFAULT_TYPE, ns.context),
            ns.flow,
            cast(User, SimpleNamespace(id=9, role=UserRole.USER, status=UserStatus.ACTIVE)),
            cast(TelegramIdentity, SimpleNamespace(telegram_user_id=123)),
        )
    )


def test_album_prompt_is_consolidated_and_edited_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = 123
    services = SimpleNamespace(
        users=SimpleNamespace(
            get_by_telegram_id=AsyncMock(
                return_value=cast(
                    User, SimpleNamespace(id=9, role=UserRole.USER, status=UserStatus.ACTIVE)
                )
            )
        )
    )

    def service_for(_context: ContextTypes.DEFAULT_TYPE) -> handlers.TelegramServices:
        return cast(handlers.TelegramServices, services)

    def identity_for(_update: Update) -> TelegramIdentity:
        return cast(TelegramIdentity, SimpleNamespace(telegram_user_id=actor))

    monkeypatch.setattr(handlers, "_services", service_for)
    monkeypatch.setattr(handlers, "_identity", identity_for)
    monkeypatch.setattr(handlers, "_is_private", _private)

    edit = AsyncMock()
    context = SimpleNamespace(
        user_data={},
        bot=SimpleNamespace(edit_message_text=edit),
    )

    def photo_update(message_id: int, file_id: str) -> tuple[Update, SimpleNamespace]:
        message = SimpleNamespace(
            photo=[SimpleNamespace(file_id=file_id)],
            message_id=message_id,
            chat_id=actor,
            media_group_id="album-9",
            caption=None,
            reply_text=AsyncMock(return_value=SimpleNamespace(message_id=777)),
        )
        update = cast(
            Update,
            SimpleNamespace(effective_message=message, update_id=message_id),
        )
        return update, message

    first_update, first_message = photo_update(10, "f-0")
    asyncio.run(
        handlers._present_photo_choices(first_update, cast(ContextTypes.DEFAULT_TYPE, context))
    )
    first_text = first_message.reply_text.await_args.args[0]
    assert messages.PHOTO_ALBUM_ONE_PRODUCT in first_text
    assert "1 şəkil seçilib" in first_text
    flow = context.user_data[PHOTO_FLOW_KEY]
    assert flow.prompt_message_id == 777
    edit.assert_not_awaited()

    second_update, second_message = photo_update(11, "f-1")
    asyncio.run(
        handlers._present_photo_choices(second_update, cast(ContextTypes.DEFAULT_TYPE, context))
    )
    second_message.reply_text.assert_not_awaited()
    edit.assert_awaited_once()
    call = edit.await_args
    assert call is not None
    kwargs = call.kwargs
    assert kwargs["message_id"] == 777
    assert "2 şəkil seçilib" in kwargs["text"]
    assert messages.PHOTO_ALBUM_ONE_PRODUCT in kwargs["text"]
    assert len(context.user_data[PHOTO_FLOW_KEY].photos) == 2


def test_album_db_single_confirmed_code_is_one_product_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ns = _setup_album(monkeypatch, holder=(uuid4(), "KA45"))
    _run_db(ns)
    ns.context.bot.send_message.assert_awaited_once()
    text = ns.context.bot.send_message.await_args.kwargs["text"]
    assert messages.PHOTO_ALBUM_CONFIRMED_HEADER.format(count=3) in text
    assert "MƏHSUL TAPILDI" in text
    assert "KA45" in text
    assert "stok" in text
    assert ns.flow.indexed_results is None
    assert ns.repo.active_confirmation_holder_by_content.await_count == 3


def test_album_db_multiple_confirmed_codes_are_reported_without_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ns = _setup_album(
        monkeypatch,
        holder_side_effect=[(uuid4(), "KA45"), (uuid4(), "KB12"), (uuid4(), "KA45")],
    )
    _run_db(ns)
    text = ns.context.bot.send_message.await_args.kwargs["text"]
    assert "KA45" in text
    assert "KB12" in text
    assert "Birdən çox təsdiqli kod" in text
    assert ns.flow.indexed_results is None


def test_album_db_combines_candidates_into_one_ranked_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    per_photo = IndexedResults((IndexedProduct("KA45", 0.93),), ())
    indexed_visual = SimpleNamespace(search=AsyncMock(return_value=per_photo))
    ns = _setup_album(monkeypatch, indexed_visual=indexed_visual)
    _run_db(ns)
    assert indexed_visual.search.await_count == 3
    assert ns.flow.indexed_results is not None
    assert [item.code for item in ns.flow.indexed_results.products] == ["KA45"]
    ns.message.reply_text.assert_awaited_once()
    text = ns.message.reply_text.await_args.args[0]
    assert messages.PHOTO_ALBUM_RESULT_HEADER.format(count=3) in text
    assert "KA45" in text
    ns.context.bot.send_message.assert_not_awaited()


def test_album_db_index_unavailable_never_invents_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    indexed_visual = SimpleNamespace(
        search=AsyncMock(return_value=IndexedResults((), (), unavailable=True))
    )
    ns = _setup_album(monkeypatch, indexed_visual=indexed_visual)
    _run_db(ns)
    text = ns.context.bot.send_message.await_args.kwargs["text"]
    assert "indeksi hazırda əlçatan deyil" in text
    assert "(3 oxundu, 0 uğursuz)" in text
    assert ns.flow.indexed_results is None
    ns.message.reply_text.assert_not_awaited()


def test_album_db_no_match_is_truthful_and_offers_save(monkeypatch: pytest.MonkeyPatch) -> None:
    indexed_visual = SimpleNamespace(search=AsyncMock(return_value=IndexedResults((), ())))
    ns = _setup_album(monkeypatch, indexed_visual=indexed_visual)
    _run_db(ns)
    text = ns.context.bot.send_message.await_args.kwargs["text"]
    assert "Təsdiqli məhsul uyğunluğu tapılmadı" in text
    assert "Yadda saxla" in text
    assert ns.flow.indexed_results is None


def test_album_db_download_failures_are_counted_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ns = _setup_album(monkeypatch, download_side_effect=OSError("network"))
    _run_db(ns)
    text = ns.context.bot.send_message.await_args.kwargs["text"]
    assert "(0 oxundu, 3 uğursuz)" in text
    assert ns.flow.indexed_results is None
    assert ns.repo.active_confirmation_holder_by_content.await_count == 0


def test_album_paging_reuses_stored_result_without_redownload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ranked = IndexedResults(tuple(IndexedProduct(f"KA{i}", 0.99 - i / 100) for i in range(6)), ())
    ns = _setup_album(monkeypatch, indexed_visual=None)
    ns.flow.indexed_results = ranked
    ns.query.data = f"pi:{ns.flow.token}:dbpg:0:0"
    asyncio.run(
        handlers.handle_photo_choice_callback(
            ns.update, cast(ContextTypes.DEFAULT_TYPE, ns.context)
        )
    )
    ns.download.assert_not_awaited()
    ns.context.bot.get_file.assert_not_awaited()
    ns.query.edit_message_text.assert_awaited_once()
    call = ns.query.edit_message_text.await_args
    assert messages.PHOTO_ALBUM_RESULT_HEADER.format(count=3) in call.args[0]
    markup = cast(InlineKeyboardMarkup, call.kwargs["reply_markup"])
    callbacks = [
        str(button.callback_data)
        for row in markup.inline_keyboard
        for button in row
        if isinstance(button.callback_data, str)
    ]
    assert f"pi:{ns.flow.token}:dbpg:0:1" in callbacks


def test_album_paging_without_index_or_stored_result_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ns = _setup_album(monkeypatch, indexed_visual=None)
    ns.flow.indexed_results = None
    ns.query.data = f"pi:{ns.flow.token}:dbpg:0:0"
    asyncio.run(
        handlers.handle_photo_choice_callback(
            ns.update, cast(ContextTypes.DEFAULT_TYPE, ns.context)
        )
    )
    ns.download.assert_not_awaited()
    text = ns.query.edit_message_text.await_args.args[0]
    assert "Tam vizual indeks hələ hazır deyil." in text


def test_new_album_after_result_starts_a_fresh_group() -> None:
    flow = add_photo(None, 123, "a", 1, 1, "album-1")
    flow.indexed_results = IndexedResults((IndexedProduct("KA45", 0.9),), ())
    fresh = add_photo(flow, 123, "b", 2, 2, "album-1")
    assert fresh.token != flow.token
    assert len(fresh.photos) == 1
    assert fresh.indexed_results is None
