"""Deterministic image header inspection: format and pixel size, never a guess."""

from dataclasses import dataclass

# One megabyte covers every JPEG segment chain that a real camera or exporter produces before the
# frame header, while keeping the scan bounded for a hostile payload.
_READ_LIMIT = 1024 * 1024
_PNG_START = b"\x89PNG\r\n\x1a\n"
_JPEG_START = b"\xff\xd8\xff"
# Start-of-frame markers carry the dimensions; these three codes inside the range do not.
_NON_FRAME_MARKERS = frozenset({0xC4, 0xC8, 0xCC})


@dataclass(frozen=True, slots=True)
class ImageSignature:
    """What the bytes say the image is. A missing size means the format does not state one."""

    mime_type: str
    format: str
    width: int | None
    height: int | None


def image_signature(data: bytes) -> ImageSignature | None:
    """Return the format and pixel size of a supported raster image, or ``None``.

    The header, not the filename, decides: an unreadable or unsupported image is reported as such
    instead of being described with invented metadata (Master Spec §23.5). Payloads reach this
    function from a file on disk or from inside a document, so it reads bytes rather than a path.
    """

    header = data[:_READ_LIMIT]
    if header.startswith(_PNG_START):
        if header[12:16] != b"IHDR" or len(header) < 24:
            return None
        return ImageSignature(
            "image/png",
            "PNG",
            int.from_bytes(header[16:20], "big"),
            int.from_bytes(header[20:24], "big"),
        )
    if header.startswith(_JPEG_START):
        size = _jpeg_dimensions(header)
        return ImageSignature("image/jpeg", "JPEG", *(size or (None, None)))
    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in {0x01, 0xD8} or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if 0xC0 <= marker <= 0xCF and marker not in _NON_FRAME_MARKERS:
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return (width, height) if width and height else None
        segment_length = int.from_bytes(data[index + 2 : index + 4], "big")
        if segment_length < 2:
            return None
        index += 2 + segment_length
    return None
