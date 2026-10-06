"""What a Telegram turn carries: the user's text plus any media attached to it.

Master Spec §22 makes an attached image a first-class search parameter, so the router needs one
place that collects media from the message itself *and* from the message it replies to. Media stays
a reference here: nothing is downloaded, stored or sent to a vendor by this module.
"""

from dataclasses import dataclass
from enum import StrEnum

from telegram import Message

IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/jpg", "image/png"})


class MediaKind(StrEnum):
    IMAGE = "image"
    DOCUMENT = "document"


@dataclass(frozen=True, slots=True)
class MediaRef:
    file_id: str
    filename: str
    media_type: str
    kind: MediaKind
    byte_size: int | None

    @property
    def is_image(self) -> bool:
        return self.kind is MediaKind.IMAGE


@dataclass(frozen=True, slots=True)
class TurnContext:
    text: str
    media: tuple[MediaRef, ...] = ()

    @property
    def images(self) -> tuple[MediaRef, ...]:
        return tuple(item for item in self.media if item.is_image)


def _media_type(mime_type: str | None, filename: str) -> str:
    declared = (mime_type or "").split(";", 1)[0].lower()
    if declared:
        return declared
    if filename.lower().endswith((".png", ".jpg", ".jpeg")):
        return "image/png" if filename.lower().endswith(".png") else "image/jpeg"
    return "application/octet-stream"


def _kind(media_type: str) -> MediaKind:
    return MediaKind.IMAGE if media_type in IMAGE_MEDIA_TYPES else MediaKind.DOCUMENT


def message_media(message: Message) -> tuple[MediaRef, ...]:
    """Media on one message: a photo, or a document that Telegram reports as a raster image."""
    if message.photo:
        largest = message.photo[-1]
        return (
            MediaRef(
                file_id=largest.file_id,
                filename=f"telegram_photo_{message.message_id}.jpg",
                media_type="image/jpeg",
                kind=MediaKind.IMAGE,
                byte_size=largest.file_size,
            ),
        )
    document = message.document
    if document is None:
        return ()
    media_type = _media_type(document.mime_type, document.file_name or "upload.bin")
    return (
        MediaRef(
            file_id=document.file_id,
            filename=document.file_name or "upload.bin",
            media_type=media_type,
            kind=_kind(media_type),
            byte_size=document.file_size,
        ),
    )


def build_turn_context(message: Message, *, text: str) -> TurnContext:
    """Collect the media the user attached to this message or is replying to."""
    referenced = message.reply_to_message
    media = [*message_media(message)]
    if referenced is not None:
        media.extend(message_media(referenced))
    unique: dict[str, MediaRef] = {}
    for item in media:
        unique.setdefault(item.file_id, item)
    return TurnContext(text=text, media=tuple(unique.values()))
