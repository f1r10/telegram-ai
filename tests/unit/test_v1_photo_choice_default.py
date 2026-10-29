"""Plain Telegram photos require explicit save, database or web choice."""

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from telegram import Update
from telegram.ext import ContextTypes

from office_assistant.integrations.telegram.handlers import handle_photo


def test_uncaptioned_photo_is_a_choice_not_an_implicit_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from office_assistant.integrations.telegram import handlers

    route = AsyncMock(return_value=False)
    choices = AsyncMock()
    upload = AsyncMock()
    lookup = AsyncMock()
    monkeypatch.setattr(handlers, "_route_caption_command", route)
    monkeypatch.setattr(handlers, "_present_photo_choices", choices)
    monkeypatch.setattr(handlers, "_handle_upload", upload)
    monkeypatch.setattr(handlers, "_handle_exact_photo_lookup", lookup)
    update = cast(
        Update,
        SimpleNamespace(
            effective_message=SimpleNamespace(
                photo=[SimpleNamespace(file_id="photo-123")],
                caption=None,
                message_id=9,
            )
        ),
    )
    context = cast(ContextTypes.DEFAULT_TYPE, SimpleNamespace())
    asyncio.run(handle_photo(update, context))
    choices.assert_awaited_once_with(update, context)
    upload.assert_not_awaited()
    lookup.assert_not_awaited()
