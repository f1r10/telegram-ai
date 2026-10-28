"""Short-lived actor-bound multi-photo selection state for Telegram.

No query-image bytes persist here; only Telegram file IDs until explicit action.
An album is one grouped ingestion session, though every image remains indexed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from secrets import token_urlsafe
from time import monotonic

from office_assistant.web_evidence.image_search import ImageWebResult

PHOTO_FLOW_KEY = "office_photo_choices_v1"
PHOTO_TTL_SECONDS = 600
MAX_ALBUM_PHOTOS = 12


@dataclass(slots=True, frozen=True)
class TelegramPhotoRef:
    file_id: str
    message_id: int
    update_id: int


@dataclass(slots=True)
class PhotoFlow:
    actor: int
    token: str
    media_group: str | None
    created_at: float
    photos: list[TelegramPhotoRef] = field(default_factory=list)
    awaiting_description: bool = False
    web_results: list[ImageWebResult] = field(default_factory=list)

    def valid(self, user: int, token: str | None = None) -> bool:
        return (
            self.actor == user
            and monotonic() - self.created_at < PHOTO_TTL_SECONDS
            and (token is None or token == self.token)
        )


def add_photo(
    old: object,
    actor: int,
    file_id: str,
    message_id: int,
    update_id: int,
    media_group: str | None,
) -> PhotoFlow:
    """Collect genuine Telegram media albums, never merge unrelated messages."""

    current = old if isinstance(old, PhotoFlow) else None
    if (
        current is None
        or not current.valid(actor)
        or current.media_group is None
        or current.media_group != media_group
        or current.awaiting_description
        or current.web_results
    ):
        current = PhotoFlow(actor, token_urlsafe(7), media_group, monotonic())
    if len(current.photos) < MAX_ALBUM_PHOTOS and not any(
        p.message_id == message_id for p in current.photos
    ):
        current.photos.append(TelegramPhotoRef(file_id, message_id, update_id))
    return current
