"""Read-only, bounded preview of a currently validated document image occurrence.

The caller must verify the actor, active source file and occurrence from the
database BEFORE calling this helper. This helper additionally checks the
storage path, source SHA-256 and actual PNG/JPEG image validity before sending
the picture through Telegram; it never writes a temporary file.
"""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from office_assistant.processing.document_visual_candidates import DocumentImageSource

MAX_PREVIEW_BYTES = 4 * 1024 * 1024
MAX_PREVIEW_PIXELS = 20_000_000


def verified_photo_preview(root: Path, source: DocumentImageSource) -> bytes | None:
    """Return safe original PNG/JPEG bytes or abstain; never trust a stored key alone."""

    root = root.resolve()
    try:
        target = (root / source.storage_key).resolve()
        target.relative_to(root)
        if not target.is_file() or not 0 < target.stat().st_size <= MAX_PREVIEW_BYTES:
            return None
        content = target.read_bytes()
        if (
            not 0 < len(content) <= MAX_PREVIEW_BYTES
            or sha256(content).hexdigest() != source.content_sha256
        ):
            return None
        with Image.open(BytesIO(content)) as picture:
            if (
                picture.format not in {"PNG", "JPEG"}
                or picture.width * picture.height > MAX_PREVIEW_PIXELS
            ):
                return None
            picture.verify()
        return content
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return None
