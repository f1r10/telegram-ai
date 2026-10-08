"""Block 5B: pictures inside a document are found where they really sit (Master Spec §23.3-§23.5).

Fixtures are built from the package formats themselves with the standard library only. Pillow is
deliberately absent here: it is installed in this environment as a transitive dependency of an
unrelated package, and a picture found inside a document must not depend on it.
"""

import json
import zipfile
import zlib
from pathlib import Path
from typing import Any, cast

import pytest
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from openpyxl import Workbook
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from office_assistant.infrastructure.database.models import ImageAssetOrigin
from office_assistant.processing import parsers as parsers_module
from office_assistant.processing.image_metadata import ImageSignature
from office_assistant.processing.models import ParsedDocument
from office_assistant.processing.parsers import ParserRegistry

PNG_START = b"\x89PNG\r\n\x1a\n"
DRAWINGML = "http://schemas.openxmlformats.org/drawingml/2006/main"
RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SPREADSHEET_DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
PACKAGE_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _png(width: int = 60, height: int = 30, color: bytes = b"\x10\xc8\x20") -> bytes:
    return (
        PNG_START
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x00" * 8
        + color
    )


def _jpeg(width: int = 64, height: int = 32) -> bytes:
    return (
        b"\xff\xd8\xff\xe0\x00\x04\x00\x00"
        + bytes([0xFF, 0xC0, 0x00, 0x11, 0x08])
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x00" * 8
    )


def _image_stream(data: bytes, *, filter_name: str, width: int, height: int) -> DecodedStreamObject:
    stream = DecodedStreamObject()
    stream.set_data(data)
    stream.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(width),
            NameObject("/Height"): NumberObject(height),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/Filter"): NameObject(filter_name),
        }
    )
    return stream


def _content_stream(operations: str) -> DecodedStreamObject:
    stream = DecodedStreamObject()
    stream.set_data(operations.encode("ascii"))
    return stream


def _indirect(writer: PdfWriter, obj: Any) -> Any:
    """Put an object in the writer and return the reference a page resource has to hold.

    pypdf gives a hand-built fixture no public way to create an indirect reference.
    """

    return cast(Any, writer)._add_object(obj)


def _form_stream(nested_name: str, nested: Any, operations: str) -> DecodedStreamObject:
    form = DecodedStreamObject()
    form.set_data(operations.encode("ascii"))
    form.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject([NumberObject(0), NumberObject(0)]),
        }
    )
    resources = DictionaryObject()
    xobjects = DictionaryObject()
    xobjects[NameObject(nested_name)] = nested
    resources[NameObject("/XObject")] = xobjects
    form[NameObject("/Resources")] = resources
    return form


def _pdf(tmp_path: Path) -> Path:
    """One picture drawn on two pages, one nested in a form, one flat, one never drawn."""

    jpeg = _jpeg()
    writer = PdfWriter()
    page_one = writer.add_blank_page(width=300, height=300)
    page_two = writer.add_blank_page(width=300, height=300)

    small_jpeg = _image_stream(
        _jpeg(width=16, height=8), filter_name="/DCTDecode", width=16, height=8
    )
    direct = _indirect(writer, _image_stream(jpeg, filter_name="/DCTDecode", width=64, height=32))
    nested = _indirect(writer, small_jpeg)
    never_drawn = _indirect(
        writer,
        _image_stream(_png(width=8, height=8), filter_name="/DCTDecode", width=8, height=8),
    )
    flat_image = _indirect(
        writer,
        _image_stream(zlib.compress(_png()), filter_name="/FlateDecode", width=60, height=30),
    )
    form = _indirect(writer, _form_stream("/ImNested", nested, "q /ImNested Do Q"))

    for page, xobjects, operations in (
        (
            page_one,
            {
                "/ImJpeg": direct,
                "/ImFlat": flat_image,
                "/ImNeverDrawn": never_drawn,
                "/Frm": form,
            },
            "q /ImJpeg Do Q q /ImFlat Do Q q /Frm Do Q",
        ),
        (page_two, {"/ImJpeg": direct}, "q /ImJpeg Do Q"),
    ):
        page_xobjects = DictionaryObject()
        for name, reference in xobjects.items():
            page_xobjects[NameObject(name)] = reference
        resources = DictionaryObject()
        resources[NameObject("/XObject")] = page_xobjects
        page[NameObject("/Resources")] = resources
        page[NameObject("/Contents")] = _indirect(writer, _content_stream(operations))

    path = tmp_path / "price-list.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def _parse(path: Path, filename: str, media_type: str) -> ParsedDocument:
    return (
        ParserRegistry().for_filename(filename).parse(path, source_file_id=7, media_type=media_type)
    )


def test_pdf_pictures_come_from_what_the_page_actually_draws(tmp_path: Path) -> None:
    parsed = _parse(_pdf(tmp_path), "price-list.pdf", "application/pdf")

    sighted = {
        (image.locator["page"], image.locator["xobject"]): image for image in parsed.embedded_images
    }

    assert sorted(sighted) == [(1, "/Frm"), (1, "/ImJpeg"), (2, "/ImJpeg")]
    # A resource the page never paints is not a sighting of that page (§23.5).
    assert sighted[(1, "/ImJpeg")].data == _jpeg()
    assert sighted[(1, "/ImJpeg")].content_type == "image/jpeg"
    assert sighted[(1, "/ImJpeg")].locator["image_index"] == 1
    assert sighted[(2, "/ImJpeg")].locator["image_index"] == 1


def test_a_picture_inside_a_form_is_located_in_the_form_not_invented_for_the_page(
    tmp_path: Path,
) -> None:
    parsed = _parse(_pdf(tmp_path), "price-list.pdf", "application/pdf")

    nested = [image for image in parsed.embedded_images if "form_xobject" in image.locator]

    # The picture the build could not store still holds its place in the page's draw order.
    assert nested[0].locator == {
        "page": 1,
        "image_index": 3,
        "xobject": "/Frm",
        "form_xobject": "/ImNested",
    }
    assert nested[0].data == _jpeg(width=16, height=8)


def test_a_flat_pdf_stream_is_reported_rather_than_rebuilt_into_a_picture(tmp_path: Path) -> None:
    parsed = _parse(_pdf(tmp_path), "price-list.pdf", "application/pdf")

    assert all(image.locator["xobject"] != "/ImFlat" for image in parsed.embedded_images)
    assert any("FlateDecode" in warning for warning in parsed.warnings)


def _docx(tmp_path: Path, *, broken_relationship: bool = False) -> Path:
    """One logo in a paragraph and the same logo in a table cell, without touching Pillow."""

    document = Document()
    document.add_paragraph("Qiymət siyahısı")
    document.add_heading("Ürvant", level=1)
    document.add_table(rows=1, cols=2)
    body = cast(Any, document.element).body
    logo = Part(
        PackURI("/word/media/image1.png"),
        "image/png",
        _png(),
        document.part.package,
    )
    relationship_id = document.part.relate_to(logo, RT.IMAGE)
    paragraphs = body.findall(_qname("w", "p"))
    paragraphs[0].append(_drawing(relationship_id))
    cells = (
        body.findall(_qname("w", "tbl"))[0].findall(_qname("w", "tr"))[0].findall(_qname("w", "tc"))
    )
    cell_paragraph = cells[1].findall(_qname("w", "p"))[0]
    cell_paragraph.append(_drawing(relationship_id))
    if broken_relationship:
        paragraphs[1].append(_drawing("rId404"))
    path = tmp_path / "letter.docx"
    document.save(str(path))
    return path


def _qname(prefix: str, local: str) -> str:
    return qn(f"{prefix}:{local}")


def _drawing(relationship_id: str) -> Any:
    return parse_xml(
        "<w:drawing "
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        f'xmlns:a="{DRAWINGML}" '
        f'xmlns:r="{RELATIONSHIPS}">'
        "<wp:inline><a:graphic><a:graphicData uri="
        f'"{DRAWINGML}/picture">'
        f'<a:blip r:embed="{relationship_id}"/>'
        "</a:graphicData></a:graphic></wp:inline></w:drawing>"
    )


def test_docx_pictures_keep_their_own_place_in_the_document(tmp_path: Path) -> None:
    parsed = _parse(
        _docx(tmp_path),
        "letter.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert len(parsed.embedded_images) == 2
    first, second = parsed.embedded_images
    assert first.locator["paragraph_index"] == 0
    assert first.locator["image_index"] == 1
    assert first.locator["relationship_id"].startswith("rId")
    assert first.data == _png()
    assert first.content_type == "image/png"
    assert second.locator["table_index"] == 0
    assert (second.locator["row"], second.locator["column"]) == (1, 2)
    assert second.data == first.data, "the same picture twice is two locators, not two files"


def test_a_docx_picture_pointing_at_nothing_is_reported(tmp_path: Path) -> None:
    parsed = _parse(
        _docx(tmp_path, broken_relationship=True),
        "letter.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert len(parsed.embedded_images) == 2
    assert any("rId404" in warning for warning in parsed.warnings)


def _xlsx(tmp_path: Path, anchors: list[tuple[int, int]]) -> Path:
    """A real workbook with one drawing part added the way a spreadsheet writer adds it."""

    workbook = Workbook()
    sheet = cast(Any, workbook.active)
    sheet.title = "Qiymətlər"
    sheet["A1"] = "KA45"
    workbook.create_sheet("Boş")
    path = tmp_path / "prices.xlsx"
    workbook.save(str(path))
    workbook.close()
    image_relationships = (
        f'<Relationships xmlns="{PACKAGE_RELATIONSHIPS}">'
        f'<Relationship Type="{RELATIONSHIPS}/image" Target="/xl/media/image1.png" Id="rId1"/>'
        "</Relationships>"
    ).encode()
    drawing_blocks = "".join(
        f"<xdr:oneCellAnchor><xdr:from><xdr:col>{column}</xdr:col><xdr:colOff>0</xdr:colOff>"
        f"<xdr:row>{row}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>"
        "<xdr:pic><xdr:blipFill>"
        f'<a:blip xmlns:a="{DRAWINGML}" xmlns:r="{RELATIONSHIPS}" r:embed="rId1"/>'
        "</xdr:blipFill></xdr:pic></xdr:oneCellAnchor>"
        for column, row in anchors
    )
    _replace_parts(
        path,
        {
            "xl/media/image1.png": _png(),
            "xl/drawings/drawing1.xml": (
                f'<xdr:wsDr xmlns:xdr="{SPREADSHEET_DRAWING}">{drawing_blocks}</xdr:wsDr>'
            ).encode(),
            "xl/drawings/_rels/drawing1.xml.rels": image_relationships,
            "xl/worksheets/_rels/sheet1.xml.rels": (
                f'<Relationships xmlns="{PACKAGE_RELATIONSHIPS}">'
                f'<Relationship Type="{RELATIONSHIPS}/drawing" '
                'Target="/xl/drawings/drawing1.xml" Id="rId1"/>'
                "</Relationships>"
            ).encode(),
        },
    )
    return path


def _replace_parts(path: Path, parts: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path) as source:
        contents = {name: source.read(name) for name in source.namelist()}
    contents.update(parts)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in contents.items():
            target.writestr(name, data)


def test_xlsx_pictures_are_anchored_to_a_named_sheet_and_cell(tmp_path: Path) -> None:
    parsed = _parse(
        _xlsx(tmp_path, [(1, 2)]),
        "prices.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    image = parsed.embedded_images[0]
    assert image.locator["sheet"] == "Qiymətlər"
    assert image.locator["anchor_cell"] == "B3"
    assert image.locator["image_index"] == 1
    assert image.data == _png()
    assert image.content_type == "image/png"
    assert parsed.warnings == [], "a workbook of its own shape parses without complaints"


def test_one_workbook_logo_in_two_cells_is_two_sightings(tmp_path: Path) -> None:
    parsed = _parse(
        _xlsx(tmp_path, [(1, 2), (4, 7)]),
        "prices.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert [image.locator["anchor_cell"] for image in parsed.embedded_images] == ["B3", "E8"]
    assert [image.locator["image_index"] for image in parsed.embedded_images] == [1, 2]
    assert parsed.embedded_images[0].data == parsed.embedded_images[1].data


def test_the_workbook_that_holds_no_pictures_reports_nothing(tmp_path: Path) -> None:
    path = tmp_path / "plain.xlsx"
    workbook = Workbook()
    cast(Any, workbook.active)["A1"] = "KA45"
    workbook.save(str(path))
    workbook.close()

    parsed = _parse(
        path,
        "plain.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert parsed.embedded_images == []
    assert parsed.warnings == []


def test_oversized_payloads_are_refused_before_the_picture_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parsers_module, "MAX_EMBEDDED_IMAGE_BYTES", 16)

    parsed = _parse(_pdf(tmp_path), "price-list.pdf", "application/pdf")

    assert parsed.embedded_images == []
    assert any("exceeds the 16 byte cap" in warning for warning in parsed.warnings)


def test_bytes_that_no_reader_can_describe_are_reported_not_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A payload the signature reader cannot place still has to be accounted for, not dropped."""

    def unreadable(data: bytes) -> ImageSignature | None:
        return None

    monkeypatch.setattr(parsers_module, "image_signature", unreadable)

    parsed = _parse(_pdf(tmp_path), "price-list.pdf", "application/pdf")

    assert parsed.embedded_images == []
    assert any("not a PNG or JPEG" in warning for warning in parsed.warnings)


def test_a_count_cap_stops_extraction_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parsers_module, "MAX_EMBEDDED_IMAGES_PER_DOCUMENT", 2)

    parsed = _parse(
        _xlsx(tmp_path, [(1, 2), (2, 3), (3, 4), (4, 5)]),
        "prices.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert len(parsed.embedded_images) == 2
    assert any("stopped at 2 images" in warning for warning in parsed.warnings)


def test_the_stored_parse_record_describes_pictures_without_holding_their_bytes(
    tmp_path: Path,
) -> None:
    parsed = _parse(
        _xlsx(tmp_path, [(1, 2)]),
        "prices.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    payload = json.dumps(parsed.as_dict(), ensure_ascii=False)

    assert json.loads(payload)["embedded_images"] == [
        {
            "locator": parsed.embedded_images[0].locator,
            "content_type": "image/png",
            "byte_size": len(_png()),
        }
    ]
    assert PNG_START not in payload.encode("utf-8")


def test_a_text_file_holds_no_pictures_and_claims_none(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("heç bir şəkil yoxdur\n", encoding="utf-8")

    parsed = _parse(path, "note.txt", "text/plain")

    assert parsed.embedded_images == []
    assert parsed.warnings == []


@pytest.mark.parametrize(
    ("filename", "origin"),
    [
        ("price-list.pdf", ImageAssetOrigin.PDF_EMBEDDED),
        ("letter.docx", ImageAssetOrigin.DOCX_EMBEDDED),
        ("prices.xlsx", ImageAssetOrigin.XLSX_EMBEDDED),
        ("note.txt", None),
        ("photo.png", None),
    ],
)
def test_each_format_announces_where_its_pictures_come_from(
    filename: str, origin: ImageAssetOrigin | None
) -> None:
    """The worker routes provenance from this annotation instead of guessing from a filename.

    A picture uploaded on its own says ``None`` because the upload is registered before the parser
    is consulted; it was never inside anything.
    """

    assert ParserRegistry().for_filename(filename).embedded_origin is origin
