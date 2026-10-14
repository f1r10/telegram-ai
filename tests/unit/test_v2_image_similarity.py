"""Near-duplicate photo suggestions: no hallucinated certainty."""

from io import BytesIO

from PIL import Image, ImageDraw

from office_assistant.processing.image_similarity import (
    VisualCandidate,
    fingerprint,
    shortlist,
)


def _synthetic_product(*, quality: int = 95, fmt: str = "JPEG") -> bytes:
    picture = Image.new("RGB", (128, 128), "white")
    pen = ImageDraw.Draw(picture)
    pen.rectangle((28, 20, 98, 113), fill=(20, 70, 150))
    pen.ellipse((38, 42, 76, 90), fill=(220, 190, 20))
    pen.line((12, 13, 117, 101), fill=(45, 100, 10), width=6)
    stream = BytesIO()
    picture.save(stream, format=fmt, quality=quality)
    return stream.getvalue()


def test_near_duplicate_photo_survives_jpeg_recompression(tmp_path: object) -> None:
    from pathlib import Path

    folder = Path(str(tmp_path))
    folder.joinpath("reference.jpg").write_bytes(_synthetic_product(quality=95))
    candidates = [VisualCandidate("KA45", "reference.jpg")]
    matches = shortlist(_synthetic_product(quality=50), candidates, folder)
    assert len(matches) == 1
    assert matches[0].product_code == "KA45"
    assert matches[0].dhash_distance <= 6
    assert matches[0].ahash_distance <= 11


def test_invalid_and_blank_pictures_do_not_create_matches(tmp_path: object) -> None:
    from pathlib import Path

    folder = Path(str(tmp_path))
    folder.joinpath("reference.jpg").write_bytes(_synthetic_product())
    references = [VisualCandidate("KA45", "reference.jpg")]
    assert shortlist(b"not an image", references, folder) == []
    blank = Image.new("RGB", (64, 64), "white")
    stream = BytesIO()
    blank.save(stream, format="PNG")
    assert fingerprint(stream.getvalue()) is None
    assert shortlist(stream.getvalue(), references, folder) == []


def test_path_traversal_cannot_read_outside_storage(tmp_path: object) -> None:
    from pathlib import Path

    folder = Path(str(tmp_path))
    outside = folder.parent / "outside-secret.jpg"
    outside.write_bytes(_synthetic_product())
    try:
        candidates = [VisualCandidate("OTHER", "../outside-secret.jpg")]
        assert shortlist(_synthetic_product(), candidates, folder) == []
    finally:
        outside.unlink(missing_ok=True)


def test_unrelated_image_is_not_forcibly_identified(tmp_path: object) -> None:
    from pathlib import Path

    folder = Path(str(tmp_path))
    folder.joinpath("reference.jpg").write_bytes(_synthetic_product())
    unrelated = Image.new("RGB", (128, 128), "white")
    pen = ImageDraw.Draw(unrelated)
    pen.polygon([(10, 10), (118, 40), (12, 80)], fill="black")
    stream = BytesIO()
    unrelated.save(stream, format="PNG")
    result = shortlist(
        stream.getvalue(),
        [VisualCandidate("KA45", "reference.jpg")],
        folder,
    )
    assert result == []
