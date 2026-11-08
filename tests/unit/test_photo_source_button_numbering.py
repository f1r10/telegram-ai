"""Ranked visual source buttons must resolve exactly the row the user read.

The DINO candidate page numbers confirmed product photos and document pictures in
one overall sequence, while only some rows carry a source file. If the file buttons
renumber themselves 1..3, "Mənbə 2" quietly delivers a different candidate's file,
which is how a correct search looked like a wrong picture.
"""

# Test the private Telegram adapter boundary without exporting it as public API.
# pyright: reportPrivateUsage=false

import asyncio
import re
from time import monotonic
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from telegram import InlineKeyboardMarkup, Message
from telegram.ext import ContextTypes

from office_assistant.infrastructure.database.models import ImageAssetOrigin
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.catalog_source_tickets import (
    CATALOG_SOURCE_TICKET_KEY,
    CatalogSourceTicket,
    issue_ranked_catalog_ticket,
    resolve_catalog_source,
    resolve_catalog_ticket,
)
from office_assistant.integrations.telegram.handlers import (
    PHOTO_FLOW_KEY,
    _send_indexed_visual_page,
)
from office_assistant.integrations.telegram.photo_flow import PhotoFlow
from office_assistant.processing.document_visual_candidates import DocumentImageSource
from office_assistant.processing.visual_ann import IndexedDocument, IndexedProduct, IndexedResults
from office_assistant.processing.visual_paging import indexed_page

ACTOR = 123


def _source(occurrence_id: int, file_id: int, name: str) -> DocumentImageSource:
    return DocumentImageSource(
        occurrence_id,
        file_id,
        name,
        ImageAssetOrigin.PDF_EMBEDDED,
        {"page": 1, "image_index": 1},
        f"blobs/{occurrence_id}.png",
        f"{occurrence_id:064x}",
        "",
    )


def _mixed_results() -> IndexedResults:
    """Products at ranks 1, 3 and 5, so page one prints documents as 2 and 4."""

    return IndexedResults(
        (IndexedProduct("KA10", 0.95), IndexedProduct("KA11", 0.93), IndexedProduct("KA12", 0.91)),
        (
            IndexedDocument(_source(31, 7, "catalog-a.pdf"), 0.94),
            IndexedDocument(_source(42, 7, "catalog-a.pdf"), 0.92),
            IndexedDocument(_source(53, 8, "katalog.xlsx"), 0.85),
        ),
    )


def _printed_document_ranks(text: str) -> list[int]:
    return [int(row) for row in re.findall(r"^(\d+)\. 🖼 Mənbə:", text, re.MULTILINE)]


def _buttons(markup: object) -> dict[str, str]:
    keyboard = cast(InlineKeyboardMarkup, markup)
    labels: dict[str, str] = {}
    for row in keyboard.inline_keyboard:
        for button in row:
            data = button.callback_data
            labels[button.text] = data if isinstance(data, str) else ""
    return labels


def test_page_exposes_the_overall_rank_of_every_document_row() -> None:
    view = indexed_page(_mixed_results(), 0)
    # Ranks 1, 3 and 5 are product photos, so the first document row is number 2.
    assert _printed_document_ranks(view.text) == [2, 4]
    assert view.doc_positions == (2, 4)
    assert [item.source.occurrence_id for item in view.docs] == [31, 42]
    # A page-local 1..2 numbering is exactly the defect this guards against.
    assert view.doc_positions != tuple(range(1, len(view.docs) + 1))
    # Paging continues the same overall ranking instead of restarting it.
    assert indexed_page(_mixed_results(), 1).doc_positions == (6,)


def test_later_pages_keep_counting_from_the_full_ranking() -> None:
    results = IndexedResults(
        tuple(IndexedProduct(f"KA{i:02d}", 0.99 - i * 0.01) for i in range(6)),
        (
            IndexedDocument(_source(71, 9, "catalog-b.pdf"), 0.93),
            IndexedDocument(_source(72, 9, "catalog-b.pdf"), 0.925),
        ),
    )
    second = indexed_page(results, 1)
    assert _printed_document_ranks(second.text) == [7, 8]
    assert second.doc_positions == (7, 8)


def _render(results: IndexedResults, page: int) -> tuple[str, dict[str, str], CatalogSourceTicket]:
    message = SimpleNamespace(reply_text=AsyncMock())
    flow = PhotoFlow(ACTOR, "tok777", None, monotonic())
    context = SimpleNamespace(user_data={PHOTO_FLOW_KEY: flow})

    asyncio.run(
        _send_indexed_visual_page(
            cast(Message, message),
            cast(ContextTypes.DEFAULT_TYPE, context),
            ACTOR,
            results,
            page=page,
        )
    )

    call = message.reply_text.await_args
    assert call is not None
    ticket = context.user_data[CATALOG_SOURCE_TICKET_KEY]
    assert isinstance(ticket, CatalogSourceTicket)
    return call.args[0], _buttons(call.kwargs["reply_markup"]), ticket


def test_source_and_preview_buttons_carry_the_printed_row_number() -> None:
    text, buttons, ticket = _render(_mixed_results(), 0)
    printed = _printed_document_ranks(text)
    assert printed == [2, 4]
    occurrences = dict(zip(printed, (31, 42), strict=True))

    for number, occurrence_id in occurrences.items():
        source_label = messages.MENU_SOURCE_FILE_BUTTON.format(number=number)
        assert source_label in buttons, f"missing {source_label} among {sorted(buttons)}"
        assert buttons[source_label] == f"cat:{ticket.token}:{number}"
        preview_label = f"🖼 Şəkil {number}-ə bax"
        assert preview_label in buttons, f"missing {preview_label}"
        assert buttons[preview_label] == f"pi:tok777:dbimg:0:0:{occurrence_id}"
        # The number the user read resolves to that row's own image occurrence.
        resolved = resolve_catalog_ticket(ticket, ACTOR, number, token=ticket.token)
        assert resolved is not None
        assert resolved.occurrence_id == occurrence_id
    # Rows 1, 3 and 5 are confirmed product photos: they own no source button, and
    # inventing "Mənbə 1/2/3" for them is what pointed at the wrong file before.
    assert not {messages.MENU_SOURCE_FILE_BUTTON.format(number=n) for n in (1, 3, 5)} & set(buttons)
    assert not {"🖼 Şəkil 1-ə bax", "🖼 Şəkil 3-ə bax", "🖼 Şəkil 5-ə bax"} & set(buttons)


def test_second_page_buttons_keep_counting_from_the_full_ranking() -> None:
    _text, buttons, ticket = _render(_mixed_results(), 1)
    source_label = messages.MENU_SOURCE_FILE_BUTTON.format(number=6)
    assert source_label in buttons
    assert buttons[source_label] == f"cat:{ticket.token}:6"
    assert buttons["🖼 Şəkil 6-ə bax"] == "pi:tok777:dbimg:0:1:53"
    assert resolve_catalog_ticket(ticket, ACTOR, 6, token=ticket.token) is not None


def test_spoken_source_falls_back_to_the_offered_position() -> None:
    ticket = issue_ranked_catalog_ticket(ACTOR, ((4, 7, 31), (7, 8, 42), (11, 9, 53)))
    assert ticket is not None
    assert [source.number for source in ticket.sources] == [4, 7, 11]
    # "birinci mənbə" names the first source actually offered, i.e. printed rank 4.
    first = resolve_catalog_source(ticket, ACTOR, 1)
    assert first is not None and first.occurrence_id == 31
    # An exact printed rank always wins over ordinal position.
    exact = resolve_catalog_source(ticket, ACTOR, 7)
    assert exact is not None and exact.occurrence_id == 42
    # Rank 2 was never printed, so it means the second offered source.
    second = resolve_catalog_source(ticket, ACTOR, 2)
    assert second is not None and second.occurrence_id == 42
    assert resolve_catalog_source(ticket, ACTOR, 9) is None
    assert resolve_catalog_source(ticket, 999, 1) is None
    expired = CatalogSourceTicket(
        token=ticket.token,
        actor_telegram_id=ACTOR,
        expires_at=monotonic() - 1,
        sources=ticket.sources,
    )
    assert resolve_catalog_source(expired, ACTOR, 1) is None
    assert resolve_catalog_source(expired, ACTOR, 4) is None


def test_ranked_ticket_is_bounded_and_validated() -> None:
    assert issue_ranked_catalog_ticket(ACTOR, ()) is None
    assert issue_ranked_catalog_ticket(0, ((1, 5, 6),)) is None
    ticket = issue_ranked_catalog_ticket(
        ACTOR,
        (
            (0, 7, 30),  # no such rank in the text
            (2, -1, 31),  # unusable file reference
            (3, 8, 42),
            (4, 8, 42),  # same occurrence printed twice cannot add a second button
            (5, 9, 53),
            (6, 10, 54),
            (7, 11, 55),  # beyond the three-button bound
        ),
    )
    assert ticket is not None
    assert [(source.number, source.occurrence_id) for source in ticket.sources] == [
        (3, 42),
        (5, 53),
        (6, 54),
    ]
