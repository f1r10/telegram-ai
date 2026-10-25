"""Native PDF image-row matching tests; synthetic documents, no customer catalog."""

# Exercise private source-layout guards without exporting them as application APIs.
# pyright: reportPrivateUsage=false

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from office_assistant.processing.pdf_visual_row import (
    _caption_for_image,
    read_pdf_visual_details,
    read_pdf_visual_row,
)


def _make_two_product_pdf(path: Path, *, text_far_away: bool = False) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=700)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    # The geometry test should not rely on a PDF renderer or external OCR.
    font_ref = writer._add_object(font)  # pyright: ignore[reportPrivateUsage]
    image = DecodedStreamObject()
    image.set_data(b"\xff\x00\x00" * 4)
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(2),
            NameObject("/Height"): NumberObject(2),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    image_ref = writer._add_object(image)  # pyright: ignore[reportPrivateUsage]
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
            NameObject("/XObject"): DictionaryObject({NameObject("/Img"): image_ref}),
        }
    )
    label_y = 180 if text_far_away else 483
    command = (
        "q 80 0 0 80 70 450 cm /Img Do Q\n"
        f"BT /F1 12 Tf 220 {label_y} Td (KONUS PLASTIK 100 SM 5 KG) Tj ET\n"
        "q 75 0 0 75 70 300 cm /Img Do Q\n"
        "BT /F1 12 Tf 220 330 Td (SURUSKEN YER) Tj ET\n"
    )
    stream = DecodedStreamObject()
    stream.set_data(command.encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)  # pyright: ignore[reportPrivateUsage]
    with path.open("wb") as out:
        writer.write(out)


def test_matched_image_receives_only_its_own_neighboring_product_row(tmp_path: Path) -> None:
    path = tmp_path / "synthetic-catalog.pdf"
    _make_two_product_pdf(path)
    assert read_pdf_visual_row(path, 1, 1) == "KONUS PLASTIK 100 SM 5 KG"
    assert read_pdf_visual_row(path, 1, 2) == "SURUSKEN YER"


def test_pdf_draw_order_is_not_visible_top_to_bottom_position(tmp_path: Path) -> None:
    path = tmp_path / "synthetic-catalog.pdf"
    _make_two_product_pdf(path)
    original = read_pdf_visual_details(path, 1, 1)
    bottom = read_pdf_visual_details(path, 1, 2)
    assert original.nearby_label == "KONUS PLASTIK 100 SM 5 KG"
    assert original.visual_row == 1
    assert bottom.visual_row == 2

    # Reverse the two Do operations while retaining their on-page positions:
    # index 1 now visually belongs to row 2, index 2 belongs to row 1.
    reader = PdfReader(str(path))
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    page = writer.pages[0]
    content = page["/Contents"].get_object()
    raw = content.get_data().decode("ascii")
    top = "q 80 0 0 80 70 450 cm /Img Do Q\n"
    bottom_img = "q 75 0 0 75 70 300 cm /Img Do Q\n"
    assert top in raw and bottom_img in raw
    raw = raw.replace(top, "").replace(bottom_img, "")
    raw = bottom_img + top + raw
    content.set_data(raw.encode("ascii"))
    with path.open("wb") as out:
        writer.write(out)
    first = read_pdf_visual_details(path, 1, 1)
    second = read_pdf_visual_details(path, 1, 2)
    assert first.visual_row == 2
    assert second.visual_row == 1


def test_missing_native_text_does_not_reuse_another_products_label(tmp_path: Path) -> None:
    path = tmp_path / "synthetic-catalog.pdf"
    _make_two_product_pdf(path, text_far_away=True)
    assert read_pdf_visual_row(path, 1, 1) is None
    assert read_pdf_visual_row(path, 1, 2) == "SURUSKEN YER"


def test_out_of_range_or_damaged_pdf_does_not_invent_product_row(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")
    assert read_pdf_visual_row(path, 1, 1) is None
    assert read_pdf_visual_row(path, 0, 1) is None
    assert read_pdf_visual_row(path, 1, 0) is None


def test_wrapped_catalog_title_and_ambiguity_controls() -> None:
    rect = (70.0, 145.0, 470.0, 560.0)
    lines = [
        (220.0, 522.0, "2 KANALLI KECEN TRAMPLIN"),
        (281.0, 507.0, "100*30*6 SM"),
    ]
    assert _caption_for_image(rect, lines) == "2 KANALLI KECEN TRAMPLIN 100*30*6 SM"
    assert _caption_for_image(rect, [(220.0, 800.0, "WRONG PRODUCT")]) is None
    assert (
        _caption_for_image(
            rect, [(220.0, 525.0, "FIRST PRODUCT"), (220.0, 490.0, "SECOND PRODUCT")]
        )
        is None
    )
