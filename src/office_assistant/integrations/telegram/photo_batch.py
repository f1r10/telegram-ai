"""Bounded, source-ordered image-album lookups without blocking all user updates.

Each photo is independently consented for web upload before this helper is
called. Only the original private Telegram file id is held in memory; query
bytes are not cached by the helper. One provider error never stops other
photos; a total deadline prevents a 12-photo album from monopolizing bot work.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from office_assistant.web_evidence.image_search import ImageWebResult

logger = logging.getLogger(__name__)
WEB_ALBUM_SECONDS = 40.0
WEB_PHOTO_SECONDS = 15.0
MAX_BATCH_PHOTOS = 12
_FALLBACK_PAGE = "https://yandex.com/images/search"


@dataclass(frozen=True, slots=True)
class WebAlbumBatch:
    results: tuple[ImageWebResult, ...]
    failed: int
    deadline_skipped: int

    @property
    def incomplete(self) -> bool:
        return bool(self.failed or self.deadline_skipped)


async def bounded_web_photo_album(
    file_ids: Sequence[str],
    *,
    download: Callable[[str], Awaitable[bytes]],
    search: Callable[[bytes], Awaitable[ImageWebResult]],
    total_seconds: float = WEB_ALBUM_SECONDS,
    per_photo_seconds: float = WEB_PHOTO_SECONDS,
) -> WebAlbumBatch:
    """Keep every input position addressable, even after a provider outage."""

    if (
        not 1 <= len(file_ids) <= MAX_BATCH_PHOTOS
        or not 0 < per_photo_seconds <= 60
        or not 0 < total_seconds <= 90
    ):
        raise ValueError("Invalid web photo batch limits")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + total_seconds
    results: list[ImageWebResult] = []
    failed = skipped = 0
    for file_id in file_ids:
        remaining = deadline - loop.time()
        if remaining <= 0:
            skipped += 1
            results.append(ImageWebResult((), _FALLBACK_PAGE, blocked=True))
            continue
        try:
            async with asyncio.timeout(min(remaining, per_photo_seconds)):
                body = await download(file_id)
                found = await search(body)
            results.append(found)
        except Exception as error:  # noqa: BLE001 - an external provider must not crash the album
            logger.info("Photo batch source failed | type=%s", type(error).__name__)
            failed += 1
            results.append(ImageWebResult((), _FALLBACK_PAGE, blocked=True))
    return WebAlbumBatch(tuple(results), failed, skipped)
