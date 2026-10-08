"""Image header inspection never describes a size the format does not state (Master Spec §23.5)."""

from pathlib import Path

from office_assistant.processing.image_metadata import read_image_signature

PNG_START = b"\x89PNG\r\n\x1a\n"


def _png(tmp_path: Path, width: int, height: int, *, chunk: bytes = b"IHDR") -> Path:
    path = tmp_path / "picture.png"
    path.write_bytes(
        PNG_START
        + (13).to_bytes(4, "big")
        + chunk
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x00" * 8
    )
    return path


def _jpeg(tmp_path: Path, marker: int, width: int, height: int, *, prefix: bytes = b"") -> Path:
    path = tmp_path / "picture.jpg"
    frame = (
        bytes([0xFF, marker, 0x00, 0x11, 0x08])
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x00" * 8
    )
    # An APP0 segment whose declared length is 4 bytes, so the walker must skip it by length.
    path.write_bytes(b"\xff\xd8\xff\xe0\x00\x04\x00\x00" + prefix + frame)
    return path


def test_png_size_comes_from_the_ihdr_chunk(tmp_path: Path) -> None:
    signature = read_image_signature(_png(tmp_path, 1234, 5678))

    assert signature is not None
    assert (signature.mime_type, signature.format) == ("image/png", "PNG")
    assert (signature.width, signature.height) == (1234, 5678)


def test_jpeg_size_comes_from_the_first_frame_marker(tmp_path: Path) -> None:
    signature = read_image_signature(_jpeg(tmp_path, 0xC0, 640, 480))

    assert signature is not None
    assert (signature.mime_type, signature.format) == ("image/jpeg", "JPEG")
    assert (signature.width, signature.height) == (640, 480)


def test_progressive_jpeg_is_read_and_stays_jpeg(tmp_path: Path) -> None:
    signature = read_image_signature(_jpeg(tmp_path, 0xC2, 800, 600))

    assert signature is not None
    assert signature.format == "JPEG"
    assert (signature.width, signature.height) == (800, 600)


def test_jpeg_segments_are_skipped_by_their_own_length(tmp_path: Path) -> None:
    # A DQT and a DHT table sit between the header and the frame; a reader that assumes a fixed
    # offset would report the table bytes as the picture size.
    dqt = bytes([0xFF, 0xDB, 0x00, 0x08]) + b"\x00" * 6
    dht = bytes([0xFF, 0xC4, 0x00, 0x08]) + b"\x00" * 6

    signature = read_image_signature(_jpeg(tmp_path, 0xC0, 320, 240, prefix=dqt + dht))

    assert signature is not None
    assert (signature.width, signature.height) == (320, 240)


def test_jpeg_without_a_frame_marker_reports_no_size(tmp_path: Path) -> None:
    # Bytes that are a real JPEG header but carry no dimensions must not invent any.
    path = tmp_path / "thumbnail.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 128)

    signature = read_image_signature(path)

    assert signature is not None
    assert signature.format == "JPEG"
    assert signature.width is None
    assert signature.height is None


def test_truncated_jpeg_is_a_jpeg_of_unknown_size(tmp_path: Path) -> None:
    path = tmp_path / "cut.jpg"
    path.write_bytes(b"\xff\xd8\xff")

    signature = read_image_signature(path)

    assert signature is not None
    assert signature.mime_type == "image/jpeg"
    assert (signature.width, signature.height) == (None, None)


def test_png_that_is_not_a_png_chunk_is_not_described(tmp_path: Path) -> None:
    path = tmp_path / "odd.png"
    path.write_bytes(PNG_START + (13).to_bytes(4, "big") + b"IEND" + b"\x00" * 16)

    assert read_image_signature(path) is None


def test_unsupported_and_truncated_bytes_are_not_an_image(tmp_path: Path) -> None:
    cases = {
        "note.txt": b"just text that no header claims to be an image",
        "tiny.png": PNG_START[:5],
        "clipped_png": PNG_START + (13).to_bytes(4, "big") + b"IHDR" + b"\x00" * 4,
        "riff.webp": b"RIFF\x00\x00\x00\x00WEBPVP8 ",
    }

    for name, data in cases.items():
        path = tmp_path / name
        path.write_bytes(data)
        assert read_image_signature(path) is None, name
