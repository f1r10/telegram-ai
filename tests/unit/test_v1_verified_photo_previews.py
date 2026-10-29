"""Document picture previews are actor-bound and verified against source bytes."""

import asyncio
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from PIL import Image
from telegram import Message
from telegram.ext import ContextTypes

from office_assistant.infrastructure.database.models import ImageAssetOrigin
from office_assistant.integrations.telegram.handlers import (
    _send_indexed_visual_page,  # pyright: ignore[reportPrivateUsage]
)
from office_assistant.integrations.telegram.photo_flow import PHOTO_FLOW_KEY, add_photo
from office_assistant.processing.document_visual_candidates import DocumentImageSource
from office_assistant.processing.verified_photo_preview import verified_photo_preview
from office_assistant.processing.visual_ann import IndexedDocument, IndexedResults


def _png() -> bytes:
    out = BytesIO()
    with Image.new("RGB", (64, 64), (64, 32, 120)) as image:
        image.save(out, "PNG")
    return out.getvalue()


def _source(key: str, digest: str) -> DocumentImageSource:
    return DocumentImageSource(
        21,
        11,
        "catalog.pdf",
        ImageAssetOrigin.PDF_EMBEDDED,
        {"page": 2},
        key,
        digest,
    )


def test_preview_refuses_changed_bytes_and_path_escape(tmp_path: Path) -> None:
    original = _png()
    (tmp_path / "valid.png").write_bytes(original)
    digest = sha256(original).hexdigest()
    assert verified_photo_preview(tmp_path, _source("valid.png", digest)) == original
    assert verified_photo_preview(tmp_path, _source("valid.png", "f" * 64)) is None
    assert verified_photo_preview(tmp_path, _source("../outside.png", digest)) is None
    (tmp_path / "valid.png").write_bytes(b"not an image")
    assert verified_photo_preview(tmp_path, _source("valid.png", digest)) is None


def test_preview_rejects_nontelegram_formats_and_missing_file(tmp_path: Path) -> None:
    gif = BytesIO()
    with Image.new("RGB", (32, 32), (255, 0, 0)) as image:
        image.save(gif, "GIF")
    data = gif.getvalue()
    (tmp_path / "photo.gif").write_bytes(data)
    assert verified_photo_preview(
        tmp_path, _source("photo.gif", sha256(data).hexdigest())
    ) is None
    assert verified_photo_preview(tmp_path, _source("gone.jpg", "0" * 64)) is None


def test_telegram_ranked_page_has_actor_bound_preview_actions() -> None:
    actor = 123
    flow = add_photo(None, actor, "photo-file", 19, 27, None)
    context = cast(
        ContextTypes.DEFAULT_TYPE,
        SimpleNamespace(user_data={PHOTO_FLOW_KEY: flow}),
    )
    message = SimpleNamespace(reply_text=AsyncMock())
    result = IndexedResults(
        (),
        (IndexedDocument(_source("picture.png", "a" * 64), 0.93),),
    )
    asyncio.run(
        _send_indexed_visual_page(
            cast(Message, message),
            context,
            actor,
            result,
        )
    )
    keyboard = message.reply_text.await_args.kwargs["reply_markup"]
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    previews = [value for value in callbacks if ":dbimg:" in value]
    assert len(previews) == 1
    assert previews[0] == f"pi:{flow.token}:dbimg:0:0:21"
    assert len(previews[0].encode()) <= 64
