"""Synthetic-only regressions: embedded PDF images are candidates, never approved products."""

from hashlib import sha256
from io import BytesIO

from PIL import Image, ImageDraw

from office_assistant.processing.catalog_visual_candidates import (
    PdfImageSource,
    match_pdf_image_sources,
    render_pdf_image_candidates,
)


def _artwork() -> bytes:
    image = Image.new("RGB", (160, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 25, 132, 130), fill="red")
    draw.polygon([(50, 35), (125, 80), (52, 127)], fill="black")
    with BytesIO() as stream:
        image.save(stream, format="PNG")
        return stream.getvalue()


def test_pdf_embed_candidate_returns_page_not_product_identity(tmp_path) -> None:
    data = _artwork()
    path = tmp_path / "catalog-picture.png"
    path.write_bytes(data)
    source = PdfImageSource(
        occurrence_id=42,
        filename="synthetic-catalog.pdf",
        page=4,
        image_index=2,
        storage_key=path.name,
        content_sha256=sha256(data).hexdigest(),
    )
    results = match_pdf_image_sources(data, [source], tmp_path)
    assert results
    assert results[0].filename == "synthetic-catalog.pdf"
    assert results[0].page == 4
    assert results[0].image_index == 2
    text = render_pdf_image_candidates(results)
    assert "synthetic-catalog.pdf" in text
    assert "səhifə 4" in text
    assert "təsdiqlənmir" in text
    assert "SKU" not in text


def test_white_padded_screenshot_matches_verified_pdf_artwork(tmp_path) -> None:
    data = _artwork()
    reference = tmp_path / "embedded.png"
    reference.write_bytes(data)
    padded = Image.new("RGB", (280, 280), "white")
    with Image.open(BytesIO(data)) as original:
        padded.paste(original, (55, 60))
    with BytesIO() as stream:
        padded.save(stream, format="PNG")
        screenshot = stream.getvalue()
    source = PdfImageSource(
        44,
        "synthetic.pdf",
        1,
        2,
        reference.name,
        sha256(data).hexdigest(),
    )
    results = match_pdf_image_sources(screenshot, [source], tmp_path)
    assert len(results) == 1
    assert results[0].page == 1


def test_corrupt_reference_and_blank_photo_fail_closed(tmp_path) -> None:
    data = _artwork()
    path = tmp_path / "bad.png"
    path.write_bytes(data)
    source = PdfImageSource(
        7,
        "catalog.pdf",
        1,
        1,
        path.name,
        "0" * 64,
    )
    assert match_pdf_image_sources(data, [source], tmp_path) == ()
    blank = Image.new("RGB", (160, 160), "white")
    with BytesIO() as stream:
        blank.save(stream, format="PNG")
        invalid = stream.getvalue()
    assert match_pdf_image_sources(invalid, [source], tmp_path) == ()


def test_invalid_pdf_source_locator_is_not_a_candidate(tmp_path) -> None:
    data = _artwork()
    path = tmp_path / "picture.png"
    path.write_bytes(data)
    no_page = PdfImageSource(1, "catalog.pdf", 0, 1, path.name, sha256(data).hexdigest())
    assert match_pdf_image_sources(data, [no_page], tmp_path) == ()


def test_untrusted_file_name_not_rendered_as_multiline_command() -> None:
    from office_assistant.processing.catalog_visual_candidates import PdfPageCandidate

    result = render_pdf_image_candidates((PdfPageCandidate("catalog.pdf\n/saxla", 2, 3),))
    assert "\n/saxla" not in result
    assert "catalog.pdf /saxla" in result
