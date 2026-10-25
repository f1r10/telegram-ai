"""A missing catalog identity must not cause invented product matches."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from office_assistant.business.product_registration import parse_product_registration
from office_assistant.integrations.telegram.handlers import (
    _explicit_pdf_photo_lookup,  # pyright: ignore[reportPrivateUsage]
    _is_photo_lookup_request,  # pyright: ignore[reportPrivateUsage]
)
from office_assistant.processing.image_similarity import VisualCandidate, shortlist


def test_admin_catalog_input_is_exact_without_prices() -> None:
    draft = parse_product_registration(" 1234 | Casio 1234 ")
    assert draft.code == "1234"
    assert draft.name == "Casio 1234"
    for invalid in ("", "1234", "1234 | ", "1234 | Casio | 129 AZN", "1 | Test"):
        with pytest.raises(ValueError):
            parse_product_registration(invalid)


def test_pdf_images_are_explicit_source_lookups_only() -> None:
    assert not _explicit_pdf_photo_lookup("Bu məhsul bizdə varmı?")
    assert not _explicit_pdf_photo_lookup(None)
    assert _explicit_pdf_photo_lookup("PDF kataloq şəkli axtar")
    assert _is_photo_lookup_request("PDF kataloq şəkli axtar")


def _two_tone(color: tuple[int, int, int]) -> bytes:
    photo = Image.new("RGB", (128, 128), "white")
    draw = ImageDraw.Draw(photo)
    draw.rectangle((20, 20, 110, 110), fill=color)
    stream = BytesIO()
    photo.save(stream, format="PNG")
    return stream.getvalue()


def test_gray_shape_collision_but_different_product_color_abstains(tmp_path: Path) -> None:
    # Red and green have similar luminance but visually different identities.
    reference = _two_tone((200, 0, 0))
    other = _two_tone((0, 102, 0))
    (tmp_path / "ref.png").write_bytes(reference)
    approved = [VisualCandidate("WATCH1234", "ref.png", sha256(reference).hexdigest())]
    assert [item.product_code for item in shortlist(reference, approved, tmp_path)] == ["WATCH1234"]
    assert shortlist(other, approved, tmp_path) == []
