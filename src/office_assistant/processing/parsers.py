"""Targeted, deterministic parser adapters and their registry."""

# Third-party document libraries do not all ship complete Pyright stubs. The
# adapter boundary keeps that uncertainty out of the normalized model/worker.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportArgumentType=false, reportOptionalMemberAccess=false

from pathlib import Path
from typing import Any, Protocol, cast

import pypdf
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
from openpyxl.cell.cell import TYPE_FORMULA
from openpyxl.worksheet.worksheet import Worksheet
from pypdf import PdfReader

from office_assistant.processing.models import ContentBlock, ParsedDocument


class DocumentParser(Protocol):
    name: str
    version: str

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument: ...


class TxtParser:
    name = "txt"
    version = "1.0"

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument:
        content = path.read_text(encoding="utf-8-sig")
        blocks = (
            [
                ContentBlock(
                    sequence=1,
                    block_type="plain_text",
                    text=content,
                    locator={"line_start": 1, "line_end": max(1, len(content.splitlines()))},
                )
            ]
            if content
            else []
        )
        return ParsedDocument(source_file_id, self.name, self.version, media_type, {}, blocks)


class PdfParser:
    name = "pypdf"
    version = pypdf.__version__

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument:
        blocks: list[ContentBlock] = []
        warnings: list[str] = []
        reader = PdfReader(str(path), strict=True)
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                blocks.append(
                    ContentBlock(
                        len(blocks) + 1,
                        "page_text",
                        str(cast(Any, text)),
                        {"page": page_number},
                    )
                )
            else:
                warnings.append(
                    f"Page {page_number} contains no extractable text; OCR may be required"
                )
        return ParsedDocument(
            source_file_id,
            self.name,
            self.version,
            media_type,
            {},
            blocks,
            warnings,
            requires_ocr=bool(warnings),
        )


class DocxParser:
    name = "python-docx"
    version = "1.2.0"

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument:
        document = Document(str(path))
        blocks: list[ContentBlock] = []
        paragraph_index = 0
        table_index = 0
        for element in document.element.body.iterchildren():
            if element.tag.endswith("}p"):
                paragraph = Paragraph(cast(Any, element), document)
                current_paragraph_index = paragraph_index
                paragraph_index += 1
                text = paragraph.text.strip()
                if text:
                    style = paragraph.style.name if paragraph.style else ""
                    block_type = "heading" if style.lower().startswith("heading") else "paragraph"
                    blocks.append(
                        ContentBlock(
                            len(blocks) + 1,
                            block_type,
                            text,
                            {"paragraph_index": current_paragraph_index},
                        )
                    )
            elif element.tag.endswith("}tbl"):
                table = Table(cast(Any, element), document)
                current_table_index = table_index
                table_index += 1
                for row_index, row in enumerate(table.rows, start=1):
                    values = [cell.text.strip() for cell in row.cells]
                    if any(values):
                        blocks.append(
                            ContentBlock(
                                len(blocks) + 1,
                                "table_row",
                                " | ".join(values),
                                {
                                    "table_index": current_table_index,
                                    "row": row_index,
                                },
                                {"cells": values},
                            )
                        )
        return ParsedDocument(source_file_id, self.name, self.version, media_type, {}, blocks)


class XlsxParser:
    name = "openpyxl"
    version = "3.1.5"

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument:
        workbook = load_workbook(path, read_only=True, data_only=False)
        blocks: list[ContentBlock] = []
        try:
            for sheet in workbook.worksheets:
                self._parse_sheet(sheet, blocks)
        finally:
            workbook.close()
        return ParsedDocument(source_file_id, self.name, self.version, media_type, {}, blocks)

    @staticmethod
    def _parse_sheet(sheet: Worksheet, blocks: list[ContentBlock]) -> None:
        for row in sheet.iter_rows():
            non_empty = [cell for cell in row if cell.value is not None]
            if not non_empty:
                continue
            values = [cell.value for cell in non_empty]
            formulas = [cell.data_type == TYPE_FORMULA for cell in non_empty]
            start = non_empty[0].coordinate
            end = non_empty[-1].coordinate
            blocks.append(
                ContentBlock(
                    len(blocks) + 1,
                    "table_row",
                    " | ".join(str(value) for value in values),
                    {"sheet": sheet.title, "range": f"{start}:{end}"},
                    {"values": values, "formulas": formulas},
                )
            )


class ImageProbeParser:
    """Verify uploaded image bytes before a vision call is allowed to spend them.

    The header, not the extension, decides the media type: a file whose bytes are not a
    supported raster image fails the run instead of being sent to the vendor.
    """

    name = "image-probe"
    version = "1.0"

    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument:
        detected = detect_image_media_type(path.read_bytes()[:12])
        if detected is None:
            raise ValueError("unsupported_image_signature")
        declared = media_type.split(";", 1)[0].lower()
        if declared not in {"", "application/octet-stream"} and declared != detected:
            raise ValueError("image_signature_mismatch")
        return ParsedDocument(
            source_file_id,
            self.name,
            self.version,
            detected,
            {"filename": path.name},
            [],
            [f"{detected} image requires vision extraction"],
            requires_vision=True,
        )


def detect_image_media_type(header: bytes) -> str | None:
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: dict[str, DocumentParser] = {
            ".txt": TxtParser(),
            ".pdf": PdfParser(),
            ".docx": DocxParser(),
            ".xlsx": XlsxParser(),
            ".jpg": ImageProbeParser(),
            ".jpeg": ImageProbeParser(),
            ".png": ImageProbeParser(),
        }

    def for_filename(self, filename: str) -> DocumentParser:
        """Route by extension.

        `application/file_validation.validate_upload` already ties the extension to the declared
        content type, and the image parser re-proves the type from the bytes themselves, so a
        second content-type lookup here would only duplicate that check.
        """

        suffix = Path(filename).suffix.lower()
        try:
            return self._parsers[suffix]
        except KeyError as error:
            raise ValueError(f"No parser registered for {suffix}") from error
