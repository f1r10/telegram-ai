"""Offline source-ticket and Telegram delivery regressions (no live data)."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

from telegram import Message
from telegram.ext import ContextTypes

from office_assistant.integrations.telegram.catalog_source_tickets import (
    CATALOG_SOURCE_TICKET_KEY,
    CatalogSourceTicket,
    issue_catalog_ticket,
    resolve_catalog_ticket,
)
from office_assistant.integrations.telegram.handlers import (
    TelegramServices,
    _catalog_source_keyboard,
    _try_catalog_source_delivery,
)
from office_assistant.internal_evidence.delivery import (
    DeliverableFile,
    parse_file_delivery_request,
)
from office_assistant.processing.catalog_visual_candidates import PdfPageCandidate


def _ticket() -> CatalogSourceTicket:
    result = issue_catalog_ticket(
        123,
        (
            PdfPageCandidate("VKLUX.pdf", 1, 4, source_file_id=8, occurrence_id=45),
        ),
    )
    assert result is not None
    return result


def test_catalog_source_ticket_is_bound_to_user_occurrence_and_expiry() -> None:
    ticket = _ticket()
    source = resolve_catalog_ticket(ticket, 123, 1, token=ticket.token)
    assert source is not None
    assert (source.file_id, source.occurrence_id) == (8, 45)
    assert resolve_catalog_ticket(ticket, 999, 1, token=ticket.token) is None
    assert resolve_catalog_ticket(ticket, 123, 1, token="forged") is None
    assert resolve_catalog_ticket(ticket, 123, 2, token=ticket.token) is None
    assert resolve_catalog_ticket(ticket, 123, 1, now=ticket.expires_at) is None


def test_tickets_reject_unresolved_sources_and_dedupe_same_pdf() -> None:
    sources = (
        PdfPageCandidate("catalog.pdf", 1, 1),
        PdfPageCandidate("catalog.pdf", 1, 2, source_file_id=8, occurrence_id=22),
        PdfPageCandidate("catalog.pdf", 3, 3, source_file_id=8, occurrence_id=23),
        PdfPageCandidate("other.pdf", 4, 1, source_file_id=9, occurrence_id=24),
    )
    ticket = issue_catalog_ticket(123, sources)
    assert ticket is not None
    assert [(entry.number, entry.file_id) for entry in ticket.sources] == [(1, 8), (2, 9)]
    assert issue_catalog_ticket(123, sources[:1]) is None


def test_catalog_answer_adds_pdf_source_button_with_short_callback() -> None:
    ticket = _ticket()
    keyboard = _catalog_source_keyboard(ticket)
    assert any(
        button.callback_data == f"cat:{ticket.token}:1"
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_colloquial_request_recognized_as_pdf_delivery() -> None:
    assert parse_file_delivery_request("həmin pdf i ver") is not False
    assert parse_file_delivery_request("bu məhsulun qiymətini ver") is False


def test_recent_catalog_ticket_delivers_exact_original_pdf(tmp_path: Path) -> None:
    ticket = _ticket()
    path = tmp_path / "VKLUX.pdf"
    path.write_bytes(b"%PDF-1.7\nsynthetic test")
    reply_document = AsyncMock()
    message = cast(
        Message,
        SimpleNamespace(reply_document=reply_document, reply_text=AsyncMock()),
    )
    context = cast(
        ContextTypes.DEFAULT_TYPE,
        SimpleNamespace(user_data={CATALOG_SOURCE_TICKET_KEY: ticket}),
    )
    source_service = Mock()
    source_service.resolve_catalog_original = AsyncMock(
        return_value=DeliverableFile(path, "VKLUX.pdf", "application/pdf", 8)
    )
    services = cast(TelegramServices, SimpleNamespace(file_delivery=source_service))
    handled = asyncio.run(
        _try_catalog_source_delivery(
            message, context, services, 123, "həmin pdf i ver"
        )
    )
    assert handled
    assert reply_document.await_count == 1
    source_service.resolve_catalog_original.assert_awaited_once_with(
        123, file_id=8, occurrence_id=45
    )
    assert reply_document.await_args is not None
    assert reply_document.await_args.kwargs["filename"] == "VKLUX.pdf"


def test_catalog_ticket_cannot_serve_other_user(tmp_path: Path) -> None:
    del tmp_path
    ticket = _ticket()
    reply_document = AsyncMock()
    reply_text = AsyncMock()
    message = cast(
        Message, SimpleNamespace(reply_document=reply_document, reply_text=reply_text)
    )
    context = cast(
        ContextTypes.DEFAULT_TYPE,
        SimpleNamespace(user_data={CATALOG_SOURCE_TICKET_KEY: ticket}),
    )
    service = Mock(resolve_catalog_original=AsyncMock())
    services = cast(TelegramServices, SimpleNamespace(file_delivery=service))
    handled = asyncio.run(
        _try_catalog_source_delivery(
            message, context, services, 999, "həmin pdf i ver"
        )
    )
    assert handled
    reply_document.assert_not_awaited()
    service.resolve_catalog_original.assert_not_awaited()
    reply_text.assert_awaited_once()
