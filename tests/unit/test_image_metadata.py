"""Image header inspection never describes a size the format does not state (Master Spec §23.5)."""

from office_assistant.processing.image_metadata import image_signature

PNG_START = b"\x89PNG\r\n\x1a\n"


def _png(width: int, height: int, *, chunk: bytes = b"IHDR") -> bytes:
    return (
        PNG_START
        + (13).to_bytes(4, "big")
        + chunk
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x00" * 8
    )


def _jpeg(marker: int, width: int, height: int, *, prefix: bytes = b"") -> bytes:
    frame = (
        bytes([0xFF, marker, 0x00, 0x11, 0x08])
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x00" * 8
    )
    # An APP0 segment whose declared length is 4 bytes, so the walker must skip it by length.
    return b"\xff\xd8\xff\xe0\x00\x04\x00\x00" + prefix + frame


def test_png_size_comes_from_the_ihdr_chunk() -> None:
    signature = image_signature(_png(1234, 5678))

    assert signature is not None
    assert (signature.mime_type, signature.format) == ("image/png", "PNG")
    assert (signature.width, signature.height) == (1234, 5678)


def test_jpeg_size_comes_from_the_first_frame_marker() -> None:
    signature = image_signature(_jpeg(0xC0, 640, 480))

    assert signature is not None
    assert (signature.mime_type, signature.format) == ("image/jpeg", "JPEG")
    assert (signature.width, signature.height) == (640, 480)


def test_progressive_jpeg_is_read_and_stays_jpeg() -> None:
    signature = image_signature(_jpeg(0xC2, 800, 600))

    assert signature is not None
    assert signature.format == "JPEG"
    assert (signature.width, signature.height) == (800, 600)


def test_jpeg_segments_are_skipped_by_their_own_length() -> None:
    # A DQT and a DHT table sit between the header and the frame; a reader that assumes a fixed
    # offset would report the table bytes as the picture size.
    dqt = bytes([0xFF, 0xDB, 0x00, 0x08]) + b"\x00" * 6
    dht = bytes([0xFF, 0xC4, 0x00, 0x08]) + b"\x00" * 6

    signature = image_signature(_jpeg(0xC0, 320, 240, prefix=dqt + dht))

    assert signature is not None
    assert (signature.width, signature.height) == (320, 240)


def test_jpeg_without_a_frame_marker_reports_no_size() -> None:
    # Bytes that are a real JPEG header but carry no dimensions must not invent any.
    signature = image_signature(b"\xff\xd8\xff\xe0" + b"\x00" * 128)

    assert signature is not None
    assert signature.format == "JPEG"
    assert signature.width is None
    assert signature.height is None


def test_truncated_jpeg_is_a_jpeg_of_unknown_size() -> None:
    signature = image_signature(b"\xff\xd8\xff")

    assert signature is not None
    assert signature.mime_type == "image/jpeg"
    assert (signature.width, signature.height) == (None, None)


def test_png_that_is_not_a_png_chunk_is_not_described() -> None:
    assert image_signature(PNG_START + (13).to_bytes(4, "big") + b"IEND" + b"\x00" * 16) is None


def test_unsupported_and_truncated_bytes_are_not_an_image() -> None:
    cases = {
        "note.txt": b"just text that no header claims to be an image",
        "tiny.png": PNG_START[:5],
        "clipped_png": PNG_START + (13).to_bytes(4, "big") + b"IHDR" + b"\x00" * 4,
        "riff.webp": b"RIFF\x00\x00\x00\x00WEBPVP8 ",
    }

    for name, data in cases.items():
        assert image_signature(data) is None, name
