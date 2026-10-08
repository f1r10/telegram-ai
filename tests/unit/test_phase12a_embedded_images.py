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
from openpyxl.reader import drawings
from pypdf import PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
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
from office_assistant.processing.pdf_images import SampleImageError, picture_bytes

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


def test_a_flat_pdf_stream_that_does_not_fill_its_grid_is_refused_by_name(tmp_path: Path) -> None:
    parsed = _parse(_pdf(tmp_path), "price-list.pdf", "application/pdf")

    assert all(image.locator["xobject"] != "/ImFlat" for image in parsed.embedded_images)
    grid = 60 * 3 * 30
    assert any(
        f"decoded samples are {len(_png())} bytes, not the {grid} the grid declares" in warning
        for warning in parsed.warnings
    ), "the rest of the page is still read, and the refusal says which picture and why"


def _raw_image(
    width: int,
    height: int,
    samples: bytes,
    *,
    color_space: str = "/DeviceRGB",
    depth: int = 8,
    filters: tuple[str, ...] = ("/FlateDecode",),
    extra: dict[str, Any] | None = None,
    compressed: bool = False,
) -> DecodedStreamObject:
    """One image stream as a PDF dictionary declares it: a grid, a colour space, and its samples.

    The caller always hands over the colour grid. ``compressed`` puts it behind the declared filter
    the way a file does, which is what a writer needs to produce a PDF a reader can open; without it
    the stream holds the samples directly, which is what a reader hands back once it has applied
    that filter.
    """

    entries: dict[str, Any] = {
        "/Type": NameObject("/XObject"),
        "/Subtype": NameObject("/Image"),
        "/Width": NumberObject(width),
        "/Height": NumberObject(height),
        "/ColorSpace": NameObject(color_space),
        "/BitsPerComponent": NumberObject(depth),
    }
    if filters:
        entries["/Filter"] = (
            NameObject(filters[0])
            if len(filters) == 1
            else ArrayObject([NameObject(name) for name in filters])
        )
    entries.update(extra or {})
    stream = DecodedStreamObject()
    stream.update({NameObject(key): value for key, value in entries.items()})
    stream.set_data(zlib.compress(samples) if compressed else samples)
    return stream


def _pdf_picture(tmp_path: Path, stream: Any) -> Path:
    """A one-page PDF that draws one picture under the name ``/Im``."""

    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    xobjects = DictionaryObject()
    xobjects[NameObject("/Im")] = _indirect(writer, stream)
    resources = DictionaryObject()
    resources[NameObject("/XObject")] = xobjects
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = _indirect(writer, _content_stream("q /Im Do Q"))
    path = tmp_path / "picture.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def _read_png(data: bytes) -> tuple[int, int, int, bytes]:
    """Size, colour type and pixel body of a PNG, read without an imaging library."""

    assert data[:8] == PNG_START
    offset = 8
    header = b""
    rows = bytearray()
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        assert zlib.crc32(kind + chunk) == int.from_bytes(data[offset + 8 + length :][:4], "big")
        if kind == b"IHDR":
            header = chunk
        elif kind == b"IDAT":
            rows += chunk
        offset += 12 + length
    assert (header[8], header[10], header[11], header[12]) == (8, 0, 0, 0)
    width = int.from_bytes(header[:4], "big")
    height = int.from_bytes(header[4:8], "big")
    color_type = header[9]
    step = _PNG_BYTES_PER_PIXEL[color_type] * width + 1
    body = zlib.decompress(bytes(rows))
    assert len(body) == step * height, "every row carries its filter byte"
    assert all(body[row * step] == 0 for row in range(height))
    pixels = b"".join(body[row * step + 1 : row * step + step] for row in range(height))
    return width, height, color_type, pixels


_PNG_BYTES_PER_PIXEL = {0: 1, 2: 3, 4: 2, 6: 4}


def test_a_pdf_picture_of_raw_samples_becomes_the_png_its_own_grid_declares(
    tmp_path: Path,
) -> None:
    """A page can hold a picture as plain colour samples; storing it as a PNG keeps the pixels."""

    samples = b"\x01\x02\x03\xfe\xfd\xfc\x10\x20\x30\x40\x50\x60"
    parsed = _parse(
        _pdf_picture(tmp_path, _raw_image(2, 2, samples, compressed=True)),
        "picture.pdf",
        "application/pdf",
    )

    image = parsed.embedded_images[0]
    assert image.content_type == "image/png"
    assert image.locator == {"page": 1, "image_index": 1, "xobject": "/Im"}
    assert parsed.warnings == ["Page 1 contains no extractable text; OCR may be required"]
    assert _read_png(image.data) == (2, 2, 2, samples)


def test_a_gray_pdf_picture_keeps_one_sample_per_pixel(tmp_path: Path) -> None:
    parsed = _parse(
        _pdf_picture(
            tmp_path,
            _raw_image(2, 2, b"\x00\x40\x80\xff", color_space="/DeviceGray", compressed=True),
        ),
        "picture.pdf",
        "application/pdf",
    )

    assert _read_png(parsed.embedded_images[0].data) == (2, 2, 0, b"\x00\x40\x80\xff")


def test_a_pdf_picture_with_no_filter_at_all_is_still_a_picture(tmp_path: Path) -> None:
    parsed = _parse(
        _pdf_picture(
            tmp_path, _raw_image(1, 3, b"\x11\x22\x33", color_space="/DeviceGray", filters=())
        ),
        "picture.pdf",
        "application/pdf",
    )

    assert _read_png(parsed.embedded_images[0].data) == (1, 3, 0, b"\x11\x22\x33")


def test_a_pdf_soft_mask_is_carried_as_the_alpha_channel_it_describes() -> None:
    stream = _raw_image(
        2,
        2,
        b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c",
        extra={"/SMask": _raw_image(2, 2, b"\x00\x40\x80\xff", color_space="/DeviceGray")},
    )

    assert _read_png(picture_bytes(stream, 1024)) == (
        2,
        2,
        6,
        b"\x01\x02\x03\x00\x04\x05\x06\x40\x07\x08\x09\x80\x0a\x0b\x0c\xff",
    )


def test_a_whole_picture_file_keeps_its_own_bytes_and_names_the_mask_it_left_behind(
    tmp_path: Path,
) -> None:
    """§23.3: a JPEG travels as the document holds it, so a mask cannot be folded into it."""

    stream = _image_stream(_jpeg(), filter_name="/DCTDecode", width=64, height=32)
    stream[NameObject("/SMask")] = _raw_image(
        64, 32, b"\xff" * (64 * 32), color_space="/DeviceGray", filters=()
    )

    parsed = _parse(_pdf_picture(tmp_path, stream), "picture.pdf", "application/pdf")

    assert parsed.embedded_images[0].data == _jpeg()
    assert parsed.warnings == [
        "Page 1 contains no extractable text; OCR may be required",
        "Page 1 picture /Im: its soft mask is not carried into the picture file that was stored",
    ]


def test_a_mask_is_only_named_beside_a_picture_that_was_actually_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two refusals must not contradict each other about whether anything was stored."""

    monkeypatch.setattr(parsers_module, "MAX_EMBEDDED_IMAGE_BYTES", 16)
    stream = _image_stream(_jpeg(), filter_name="/DCTDecode", width=64, height=32)
    stream[NameObject("/SMask")] = _raw_image(
        64, 32, b"\xff" * (64 * 32), color_space="/DeviceGray", filters=()
    )

    parsed = _parse(_pdf_picture(tmp_path, stream), "picture.pdf", "application/pdf")

    assert parsed.embedded_images == []
    assert [warning for warning in parsed.warnings if "picture" in warning] == [
        f"Page 1 picture /Im: {len(_jpeg())} bytes exceeds the 16 byte cap"
    ]


class _UndecodableStream:
    """An image stream whose declared decoder fails, the way a reader reports a lying file."""

    def __init__(self, entries: dict[str, Any]) -> None:
        self._entries = entries

    def get(self, key: str, default: Any = None) -> Any:
        return self._entries.get(key, default)

    def get_data(self) -> bytes:
        raise PdfReadError("could not inflate the samples")


def test_a_stream_the_reader_cannot_open_costs_one_picture_not_the_document() -> None:
    """A declared filter that cannot deliver is a named refusal, never an escaping exception."""

    stream = _UndecodableStream(
        {
            "/Subtype": NameObject("/Image"),
            "/Filter": NameObject("/FlateDecode"),
            "/ColorSpace": NameObject("/DeviceGray"),
            "/BitsPerComponent": NumberObject(8),
            "/Width": NumberObject(2),
            "/Height": NumberObject(2),
        }
    )

    with pytest.raises(SampleImageError) as refused:
        picture_bytes(stream, 1024)

    assert str(refused.value) == "/FlateDecode stream will not decode (PdfReadError)"


@pytest.mark.parametrize(
    ("stream", "reason"),
    [
        (
            _raw_image(2, 2, b"\x00" * 12, extra={"/ImageMask": BooleanObject(True)}),
            "a stencil mask is not a picture",
        ),
        (
            _raw_image(2, 2, b"\x00" * 4, color_space="/DeviceCMYK"),
            "colour space /DeviceCMYK needs a mapping nobody declared",
        ),
        (
            _raw_image(2, 2, b"\x00" * 4, color_space="/Indexed"),
            "colour space /Indexed needs a mapping nobody declared",
        ),
        (_raw_image(2, 2, b"\x00" * 12, depth=4), "bit depth 4 is not unpacked here"),
        (_raw_image(0, 2, b""), "dictionary declares no usable /Width and /Height grid"),
        (
            _raw_image(2, 2, b"\x00" * 12, filters=("/FlateDecode", "/ASCIIHexDecode")),
            "filter chain /FlateDecode+/ASCIIHexDecode is not one encoder",
        ),
        (
            _raw_image(2, 2, b"\x00" * 12, filters=("/CCITTFaxDecode",)),
            "stream filter /CCITTFaxDecode holds samples no rule here reinterpret",
        ),
        (
            _raw_image(2, 2, b"\x00" * 12, extra={"/Decode": ArrayObject([NumberObject(0)])}),
            "/Decode stands between these samples and the painted colour",
        ),
        (
            _raw_image(
                2,
                2,
                b"\x00" * 12,
                extra={"/Mask": _raw_image(2, 2, b"\x00" * 4, color_space="/DeviceGray")},
            ),
            "/Mask stands between these samples and the painted colour",
        ),
        (_raw_image(400, 400, b"\x00" * 12), "declared grid 400x400 is over the 500 byte bound"),
        (
            _raw_image(2, 2, b"\x00" * 11),
            "decoded samples are 11 bytes, not the 12 the grid declares",
        ),
        (
            _raw_image(2, 1, b"\x00" * 6, extra={"/SMask": _raw_image(2, 1, b"\x00" * 2)}),
            "soft mask is not a plain gray rectangle",
        ),
        (
            _raw_image(
                2,
                1,
                b"\x00" * 6,
                extra={"/SMask": _raw_image(3, 1, b"\x00" * 3, color_space="/DeviceGray")},
            ),
            "soft mask covers grid (3, 1), not the picture's (2, 1)",
        ),
        (
            _raw_image(
                2,
                1,
                b"\x00" * 6,
                extra={"/SMask": _raw_image(2, 1, b"\x00" * 4, color_space="/DeviceGray", depth=1)},
            ),
            "soft mask is not 8-bit",
        ),
        (
            _raw_image(
                2,
                1,
                b"\x00" * 6,
                extra={
                    "/SMask": _raw_image(
                        2, 1, b"\x00" * 4, color_space="/DeviceGray", filters=("/JPXDecode",)
                    )
                },
            ),
            "stream filter /JPXDecode holds samples no rule here reinterpret",
        ),
        (
            _raw_image(
                2,
                1,
                b"\x00" * 6,
                extra={"/SMask": _raw_image(2, 1, b"\x00" * 3, color_space="/DeviceGray")},
            ),
            "soft mask samples do not fill the declared grid",
        ),
    ],
)
def test_a_picture_this_build_cannot_rebuild_is_refused_by_name(stream: Any, reason: str) -> None:
    """Every refusal names the entry that caused it, so a reader can tell a gap from a guess."""

    with pytest.raises(SampleImageError) as refused:
        picture_bytes(stream, 500)

    assert str(refused.value) == reason


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


def _paragraph_element(paragraph: Any) -> Any:
    """The XML element under a paragraph, which is where a hand-built drawing has to go."""

    return paragraph._p


def _docx_with_letterhead(tmp_path: Path) -> Path:
    """A logo in the body and a different one carried only by the section's header and footer."""

    document = Document()
    document.add_paragraph("Qiymət siyahısı")
    package = document.part.package

    def attach(part: Any, element: Any, name: str, image: bytes) -> None:
        """Give one package part a picture of its own and draw it in one of its elements."""

        logo = Part(PackURI(name), "image/png", image, package)
        element.append(_drawing(part.relate_to(logo, RT.IMAGE)))

    body_paragraph = cast(Any, document.element).body.findall(_qname("w", "p"))[0]
    attach(document.part, body_paragraph, "/word/media/image1.png", _png())
    header = document.sections[0].header
    footer = document.sections[0].footer
    header.is_linked_to_previous = False
    footer.is_linked_to_previous = False
    attach(
        header.part,
        _paragraph_element(header.paragraphs[0]),
        "/word/media/image2.png",
        _png(width=120, height=20),
    )
    attach(
        footer.part,
        _paragraph_element(footer.paragraphs[0]),
        "/word/media/image3.png",
        _png(width=20, height=8),
    )
    path = tmp_path / "letterhead.docx"
    document.save(str(path))
    return path


def test_a_logo_carried_only_by_the_header_or_footer_is_still_sighted(tmp_path: Path) -> None:
    """A letterhead lives in its own part, so its locator names that part instead of the body."""

    parsed = _parse(
        _docx_with_letterhead(tmp_path),
        "letterhead.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    body, header, footer = parsed.embedded_images
    assert "part" not in body.locator
    assert body.locator["paragraph_index"] == 0
    assert header.locator["part"] == "word/header1.xml"
    assert (header.locator["section_index"], header.locator["role"]) == (0, "default_header")
    assert (header.locator["paragraph_index"], header.locator["image_index"]) == (0, 1)
    assert header.locator["relationship_id"].startswith("rId")
    assert header.data == _png(width=120, height=20)
    assert footer.locator["part"] == "word/footer1.xml"
    assert footer.locator["role"] == "default_footer"
    assert footer.data == _png(width=20, height=8)
    assert parsed.warnings == []


def test_a_section_that_links_its_header_owns_no_picture_of_its_own(
    tmp_path: Path,
) -> None:
    """Reaching for a header definition that is not there must not invent one, here or on disk."""

    path = _docx(tmp_path)
    parsed = _parse(
        path,
        "letter.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert [image.locator.get("role") for image in parsed.embedded_images] == [None, None]
    with zipfile.ZipFile(path) as archive:
        assert not [name for name in archive.namelist() if "header" in name or "footer" in name]


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


def test_workbook_pictures_do_not_depend_on_the_imaging_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """openpyxl's own drawing reader returns nothing once Pillow is absent, so extraction avoids it.

    This pins the reason the parser walks the package itself rather than reading ``sheet._images``:
    an imaging library is not a dependency of this build (Q28), and a workbook picture silently
    disappearing from the product would be worse than one extra zip pass.
    """

    monkeypatch.setattr(drawings, "PILImage", False)
    path = _xlsx(tmp_path, [(1, 2)])
    with zipfile.ZipFile(path) as archive:
        assert drawings.find_images(archive, "xl/drawings/drawing1.xml") == ([], [])

    parsed = _parse(
        path,
        "prices.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert [image.locator["anchor_cell"] for image in parsed.embedded_images] == ["B3"]
    assert parsed.embedded_images[0].data == _png()


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
