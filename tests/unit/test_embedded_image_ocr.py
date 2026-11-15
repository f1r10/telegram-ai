"""M10: a picture inside a document becomes searchable, at the place it really sits.

The fixtures are real DOCX/XLSX/PDF packages built here, without Pillow: the picture payload only
has to be a PNG or JPEG this build can describe, because the parser never decodes pixels. OCR is a
stub provider, so the assertions are about routing, locators and caps, not about Tesseract's
accuracy.
"""

import zipfile
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
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from office_assistant.global_intelligence.providers import (
    RECOGNITION_FAILED,
    OcrProviderError,
    OcrResult,
)
from office_assistant.infrastructure.database.models import DocumentProcessingStatus
from office_assistant.processing import image_ocr as image_ocr_module
from office_assistant.processing import parsers as parsers_module
from office_assistant.processing.image_ocr import ImageOcrService
from office_assistant.processing.parsers import ParserRegistry
from office_assistant.processing.worker_service import searchable_parse_status

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF = "application/pdf"

PNG_START = b"\x89PNG\r\n\x1a\n"
DRAWINGML = "http://schemas.openxmlformats.org/drawingml/2006/main"
RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
SPREADSHEET_DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
PACKAGE_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"


class StubOcrProvider:
    """Fixed text, or one chosen failure; records what it was asked to read."""

    name = "stub"
    version = "test"
    supported_languages = frozenset({"aze", "eng", "rus"})

    def __init__(
        self,
        *,
        text: str = "",
        confidence: float | None = None,
        error: Exception | None = None,
    ) -> None:
        self.text = text
        self.confidence = confidence
        self.error = error
        self.calls: list[tuple[int, float, int]] = []

    def recognize(
        self,
        image: bytes,
        *,
        languages: tuple[str, ...],
        psm: int,
        timeout_seconds: float,
    ) -> OcrResult:
        self.calls.append((psm, timeout_seconds, len(image)))
        if self.error is not None:
            raise self.error
        return OcrResult(
            text=self.text,
            confidence=self.confidence,
            engine=self.name,
            version=self.version,
            languages=languages,
            warnings=(),
        )


def _png(width: int = 60, height: int = 30) -> bytes:
    return (
        PNG_START
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x00" * 8
    )


def _jpeg(width: int = 64, height: int = 32) -> bytes:
    return (
        b"\xff\xd8\xff\xe0\x00\x04\x00\x00"
        + bytes([0xFF, 0xC0, 0x00, 0x11, 0x08])
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x00" * 8
    )


def _registry(provider: StubOcrProvider | None, *, gate: bool = True) -> ParserRegistry:
    return ParserRegistry(
        image_ocr=ImageOcrService(provider=provider) if provider is not None else None,
        ocr_embedded_images=gate,
    )


def _parse(registry: ParserRegistry, path: Path, filename: str, media_type: str) -> Any:
    return registry.for_filename(filename).parse(path, source_file_id=11, media_type=media_type)


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


def _docx(tmp_path: Path, *, with_picture: bool = True) -> Path:
    """One paragraph, one heading and a table; a logo in the paragraph and in a cell."""

    document = Document()
    document.add_paragraph("Qiymət siyahısı")
    document.add_heading("Ürvant", level=1)
    document.add_table(rows=1, cols=2)
    if with_picture:
        body = cast(Any, document.element).body
        logo = Part(PackURI("/word/media/image1.png"), "image/png", _png(), document.part.package)
        relationship_id = document.part.relate_to(logo, RT.IMAGE)
        paragraphs = body.findall(qn("w:p"))
        paragraphs[0].append(_drawing(relationship_id))
        cells = body.findall(qn("w:tbl"))[0].findall(qn("w:tr"))[0].findall(qn("w:tc"))
        cells[1].findall(qn("w:p"))[0].append(_drawing(relationship_id))
    path = tmp_path / ("letter.docx" if with_picture else "plain.docx")
    document.save(str(path))
    return path


def _indirect(writer: Any, obj: Any) -> Any:
    """Register one object in the PDF's object table, the way the writer's own API does."""

    return writer._add_object(obj)


def _replace_parts(path: Path, parts: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path) as source:
        contents = {name: source.read(name) for name in source.namelist()}
    contents.update(parts)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in contents.items():
            target.writestr(name, data)


def _xlsx(tmp_path: Path, *, with_picture: bool = True) -> Path:
    """A real workbook whose drawing part anchors one picture on B3."""

    workbook = Workbook()
    sheet = cast(Any, workbook.active)
    sheet.title = "Qiymətlər"
    sheet["A1"] = "KA45"
    path = tmp_path / ("prices.xlsx" if with_picture else "plain.xlsx")
    workbook.save(str(path))
    workbook.close()
    if not with_picture:
        return path
    drawing = (
        f'<xdr:wsDr xmlns:xdr="{SPREADSHEET_DRAWING}">'
        "<xdr:oneCellAnchor><xdr:from><xdr:col>1</xdr:col><xdr:colOff>0</xdr:colOff>"
        "<xdr:row>2</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>"
        "<xdr:pic><xdr:blipFill>"
        f'<a:blip xmlns:a="{DRAWINGML}" xmlns:r="{RELATIONSHIPS}" r:embed="rId1"/>'
        "</xdr:blipFill></xdr:pic></xdr:oneCellAnchor></xdr:wsDr>"
    )
    _replace_parts(
        path,
        {
            "xl/media/image1.png": _png(),
            "xl/drawings/drawing1.xml": drawing.encode(),
            "xl/drawings/_rels/drawing1.xml.rels": (
                f'<Relationships xmlns="{PACKAGE_RELATIONSHIPS}">'
                f'<Relationship Type="{RELATIONSHIPS}/image" '
                'Target="/xl/media/image1.png" Id="rId1"/>'
                "</Relationships>"
            ).encode(),
            "xl/worksheets/_rels/sheet1.xml.rels": (
                f'<Relationships xmlns="{PACKAGE_RELATIONSHIPS}">'
                f'<Relationship Type="{RELATIONSHIPS}/drawing" '
                'Target="/xl/drawings/drawing1.xml" Id="rId1"/>'
                "</Relationships>"
            ).encode(),
        },
    )
    return path


def _pdf(tmp_path: Path, *, with_picture: bool = True) -> Path:
    """One page that draws a JPEG under the name ``/Im`` (or an empty page)."""

    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    if with_picture:
        stream = DecodedStreamObject()
        stream.set_data(_jpeg())
        stream.update(
            {
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Filter"): NameObject("/DCTDecode"),
                NameObject("/Width"): NumberObject(64),
                NameObject("/Height"): NumberObject(32),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                NameObject("/BitsPerComponent"): NumberObject(8),
            }
        )
        xobjects = DictionaryObject()
        xobjects[NameObject("/Im")] = _indirect(writer, stream)
        resources = DictionaryObject()
        resources[NameObject("/XObject")] = xobjects
        page[NameObject("/Resources")] = resources
        contents = DecodedStreamObject()
        contents.set_data(b"q 200 0 0 200 0 0 cm /Im Do Q")
        page[NameObject("/Contents")] = _indirect(writer, contents)
    path = tmp_path / ("scan.pdf" if with_picture else "plain.pdf")
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_pdf_picture_text_is_recorded_at_the_page_and_xobject_it_was_drawn_under(
    tmp_path: Path,
) -> None:
    provider = StubOcrProvider(text="Model KA45 220V")
    parsed = _parse(_registry(provider), _pdf(tmp_path), "scan.pdf", PDF)

    assert [block.block_type for block in parsed.blocks] == ["image_ocr"]
    block = parsed.blocks[0]
    assert block.text == "Model KA45 220V"
    assert block.locator == {
        "page": 1,
        "image_index": 1,
        "xobject": "/Im",
        "extraction": "tesseract",
    }
    # The stored picture keeps its own extraction locator, unmoved by the added block.
    assert parsed.embedded_images[0].locator == {"page": 1, "image_index": 1, "xobject": "/Im"}
    assert parsed.metadata["image_ocr"] == {
        "attempted": 1,
        "recovered": 1,
        "unresolved": 0,
    }
    # The text is now citable content, so the run is searchable rather than deferred.
    assert searchable_parse_status(parsed) == DocumentProcessingStatus.SUCCEEDED
    assert provider.calls == [(6, 15.0, len(_jpeg()))], "one bounded engine call, psm 6 as uploads"


def test_docx_picture_text_keeps_its_paragraph_and_its_table_cell_apart(tmp_path: Path) -> None:
    provider = StubOcrProvider(text="Ürvant KA45", confidence=0.87)
    path = _docx(tmp_path)
    gated = _parse(_registry(provider), path, path.name, DOCX)
    ungated = _parse(_registry(None, gate=False), path, path.name, DOCX)

    image_blocks = [block for block in gated.blocks if block.block_type == "image_ocr"]
    assert len(image_blocks) == 2
    first, second = image_blocks
    assert first.locator["paragraph_index"] == 0
    assert first.locator["image_index"] == 1
    assert first.locator["relationship_id"].startswith("rId")
    assert first.locator["extraction"] == "tesseract"
    assert (second.locator["table_index"], second.locator["row"]) == (0, 1)
    assert second.locator["column"] == 2
    assert first.text == second.text == "Ürvant KA45"
    # Recording nothing about confidence when the flag is off keeps the block to its facts.
    assert first.metadata == {}
    assert gated.metadata["image_ocr"] == {"attempted": 2, "recovered": 2, "unresolved": 0}
    # Additive only: the document's own blocks keep every sequence number they had.
    assert gated.blocks[: len(ungated.blocks)] == ungated.blocks
    assert len(gated.blocks) == len(ungated.blocks) + 2


def test_docx_picture_confidence_is_recorded_only_when_it_was_asked_for(tmp_path: Path) -> None:
    path = _docx(tmp_path)
    provider = StubOcrProvider(text="Ürvant KA45", confidence=0.87)
    with_confidence = ParserRegistry(
        image_ocr=ImageOcrService(provider=provider),
        ocr_embedded_images=True,
        capture_confidence=True,
    )
    without = ParserRegistry(
        image_ocr=ImageOcrService(provider=provider),
        ocr_embedded_images=True,
    )

    scored = _parse(with_confidence, path, path.name, DOCX).blocks[-1]
    plain = _parse(without, path, path.name, DOCX).blocks[-1]

    assert scored.metadata == {"extraction": "tesseract", "confidence": 0.87}
    assert plain.metadata == {}


def test_xlsx_picture_text_points_at_the_sheet_and_the_anchor_cell(tmp_path: Path) -> None:
    provider = StubOcrProvider(text="Stok: 8 ədəd")
    path = _xlsx(tmp_path)
    parsed = _parse(_registry(provider), path, path.name, XLSX)

    ocr_block = [block for block in parsed.blocks if block.block_type == "image_ocr"][0]
    assert ocr_block.locator == {
        "sheet": "Qiymətlər",
        "image_index": 1,
        "relationship_id": "rId1",
        "anchor_cell": "B3",
        "extraction": "tesseract",
    }
    assert "Stok: 8 ədəd" in parsed.text()
    assert parsed.metadata["image_ocr"]["recovered"] == 1


@pytest.mark.parametrize(
    ("builder", "filename", "media_type"),
    [
        (_docx, "letter.docx", DOCX),
        (_xlsx, "prices.xlsx", XLSX),
        (_pdf, "scan.pdf", PDF),
    ],
)
def test_a_document_with_no_pictures_is_unchanged_by_the_gate(
    tmp_path: Path,
    builder: Any,
    filename: str,
    media_type: str,
) -> None:
    path = builder(tmp_path, with_picture=False)
    provider = StubOcrProvider(text="should never be read")

    gated = _parse(_registry(provider), path, filename, media_type)
    ungated = _parse(_registry(None, gate=False), path, filename, media_type)

    assert gated.as_dict() == ungated.as_dict()
    assert "image_ocr" not in gated.metadata
    assert provider.calls == []


def test_a_provider_failure_stores_the_picture_but_claims_no_text(tmp_path: Path) -> None:
    provider = StubOcrProvider(error=OcrProviderError(RECOGNITION_FAILED))
    path = _pdf(tmp_path)
    failed = _parse(_registry(provider), path, path.name, PDF)
    ungated = _parse(_registry(None, gate=False), path, path.name, PDF)

    assert len(failed.embedded_images) == 1, "the picture is still an asset of the document"
    assert failed.blocks == ungated.blocks, "no block is invented for an unreadable picture"
    assert failed.warnings == [
        *ungated.warnings,
        "Page 1 picture /Im: IMAGE_OCR_RECOGNITION_FAILED",
    ]
    assert failed.metadata["image_ocr"] == {
        "attempted": 1,
        "recovered": 0,
        "unresolved": 1,
    }


def test_the_ocr_pass_stops_at_its_own_count_and_says_how_many_were_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parsers_module, "MAX_EMBEDDED_IMAGE_OCR", 1)
    provider = StubOcrProvider(text="KA45")
    path = _docx(tmp_path)  # two pictures: one in the paragraph, one in the table cell

    parsed = _parse(_registry(provider), path, path.name, DOCX)

    assert [block.block_type for block in parsed.blocks].count("image_ocr") == 1
    assert len(provider.calls) == 1, "the cap stops the pass, not just its result list"
    assert len(parsed.embedded_images) == 2, "the unread picture still keeps its stored bytes"
    assert parsed.metadata["image_ocr"] == {
        "attempted": 1,
        "recovered": 1,
        "unresolved": 0,
    }
    assert parsed.warnings == [
        "Embedded-picture OCR stopped at 1 pictures; the rest keep their stored bytes and are "
        "not read as text"
    ]


def test_a_picture_above_the_image_size_cap_is_refused_without_running_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(image_ocr_module, "MAX_INPUT_BYTES", 8)
    provider = StubOcrProvider(text="KA45")
    path = _pdf(tmp_path)

    parsed = _parse(_registry(provider), path, path.name, PDF)

    assert parsed.blocks == []
    assert provider.calls == [], "the size cap is checked before any process is started"
    assert parsed.metadata["image_ocr"] == {
        "attempted": 1,
        "recovered": 0,
        "unresolved": 1,
    }
    assert "Page 1 picture /Im: IMAGE_OCR_SIZE_LIMIT" in parsed.warnings


def test_the_gate_needs_the_image_service_as_well_as_the_milestone_flag(tmp_path: Path) -> None:
    """IMAGE_OCR_ENABLED off means no service, so the gate on its own reads nothing."""

    path = _pdf(tmp_path)
    parsed = _parse(_registry(None), path, path.name, PDF)

    assert [block.block_type for block in parsed.blocks] == []
    assert "image_ocr" not in parsed.metadata
    assert len(parsed.embedded_images) == 1
