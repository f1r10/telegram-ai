"""Deterministically generated binary fixtures for Phase-2 parser/OCR measurement.

The repository stays **binary-free** (task brief S2 rule 4; document-intelligence audit risk R7):
every fixture is built here, at test time, into a pytest temp dir from the existing synthetic
corpus text, and is removed when the run ends. Nothing in this module is committed as bytes.

The four formats Phase 2 needs a baseline for are generated without a new dependency:

* PDF — a minimal single-page file with a base-14 Helvetica font, so ``pypdf`` can extract the text;
* DOCX — ``python-docx`` headings and a numbered list;
* XLSX — ``openpyxl`` with two sheets, a shared key and a formula (the M5/M7 shapes);
* PNG — a Pillow raster carrying ASCII text for the OCR path.

Rendering is deliberately plain: generated text proves a parser *finds* text, not that it reads
real phone-photo Azerbaijani accurately (TEST_STRATEGY.md §3.3).
"""

# Third-party document libraries do not all ship complete Pyright stubs; this is the fixture
# boundary, exactly like ``processing/parsers.py``.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportOptionalMemberAccess=false

from __future__ import annotations

import zipfile
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path

from docx import Document
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from .corpus import build_reference_corpus

PDF_SIGNATURE = b"%PDF-"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_OOXML_CONTENT_TYPES = "[Content_Types].xml"
_TEXT_WIDTH = 78


def _ascii(value: str) -> str:
    """Fold text to the ASCII the base-14 Helvetica encoding and the default bitmap font carry."""

    return "".join(character if 32 <= ord(character) < 127 else "?" for character in value)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in text.split(".") if part.strip()]


def _wrap(text: str, width: int = _TEXT_WIDTH) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in _ascii(text).split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_bytes(lines: Sequence[str]) -> bytes:
    """Build a minimal valid single-page PDF whose text ``pypdf`` can extract.

    A correct cross-reference table is written by hand so the file is a real PDF rather than a
    stub that merely starts with ``%PDF-``. Bytes are deterministic (no dates, no compression).
    """

    drawing = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            drawing.append("T*")
        drawing.append(f"({_escape(line)}) Tj")
    drawing.append("ET")
    content = "\n".join(drawing).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = bytearray(f"{PDF_SIGNATURE.decode()}1.4\n".encode("ascii"))
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_offset = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    trailer = f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    out += trailer.encode("ascii")
    return bytes(out)


def docx_bytes(sections: Sequence[tuple[int, str]]) -> bytes:
    """Build a DOCX from ``(heading_level, text)`` pairs; level 0 becomes a numbered list item."""

    document = Document()
    for level, text in sections:
        if level:
            document.add_heading(text, level=level)
        else:
            document.add_paragraph(text, style="List Number")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def xlsx_bytes(sheets: Sequence[tuple[str, Sequence[Sequence[object]]]]) -> bytes:
    """Build an XLSX from ``(sheet_title, rows)`` pairs, preserving a formula cell as written."""

    workbook = Workbook()
    default = workbook.active
    for index, (title, rows) in enumerate(sheets):
        worksheet = default if index == 0 else workbook.create_sheet()
        worksheet.title = title
        for row in rows:
            worksheet.append(list(row))
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def png_bytes(text: str, size: tuple[int, int] = (360, 96)) -> bytes:
    """Render ``text`` into a real PNG raster with Pillow's built-in bitmap font."""

    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).text((12, 36), _ascii(text), fill="black", font=ImageFont.load_default())
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def is_well_formed_pdf(data: bytes) -> bool:
    """True when ``data`` is a PDF ``pypdf`` can open and that has at least one page."""

    if not data.startswith(PDF_SIGNATURE) or b"%%EOF" not in data[-2048:]:
        return False
    try:
        return len(PdfReader(BytesIO(data)).pages) >= 1
    except Exception:
        return False


def is_well_formed_ooxml(data: bytes) -> bool:
    """True when ``data`` is a readable OOXML package (DOCX/XLSX are zip containers)."""

    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            return archive.testzip() is None and _OOXML_CONTENT_TYPES in archive.namelist()
    except zipfile.BadZipFile:
        return False


def is_well_formed_png(data: bytes) -> bool:
    """True when ``data`` starts with the PNG signature and its first chunk is ``IHDR``."""

    return data.startswith(PNG_SIGNATURE) and data[12:16] == b"IHDR"


_FIXTURE_NAMES = ("simple.pdf", "headings.docx", "multi_sheet.xlsx", "sample.png")

_XLSX_SHEETS: tuple[tuple[str, tuple[tuple[object, ...], ...]], ...] = (
    (
        "Qaliq",
        (
            ("Kod", "Ad", "Miqdar"),
            ("PRN-M404", "HP LaserJet Pro M404dn", 4),
            ("CAM-2143", "Hikvision DS-2CD2143G2-I", 12),
        ),
    ),
    (
        "Satis",
        (
            ("Kod", "Miqdar"),
            ("PRN-M404", 2),
            ("CAM-2143", 5),
            ("Cemi", "=SUM(B2:B3)"),
        ),
    ),
)


def generate_fixtures(directory: Path) -> dict[str, Path]:
    """Write the four M0 fixtures into ``directory`` and return the ``name -> path`` mapping."""

    directory.mkdir(parents=True, exist_ok=True)
    corpus = build_reference_corpus()
    warranty = corpus.by_id("docx-warranty-policy-printers").text
    sentences = _sentences(warranty)
    sections: list[tuple[int, str]] = [(1, sentences[0] if sentences else "Zemanet siyaseti")]
    sections += [(2, sentence) for sentence in sentences[1:3]]
    sections += [(3, sentence) for sentence in sentences[3:4]]
    sections += [(0, "Zemanet muddeti 24 ay"), (0, "Rəsmi servis təsdiqi tələb olunur")]

    fixtures = {name: directory / name for name in _FIXTURE_NAMES}
    fixtures["simple.pdf"].write_bytes(
        pdf_bytes(_wrap(corpus.by_id("pdf-printer-m404-datasheet").text))
    )
    fixtures["headings.docx"].write_bytes(docx_bytes(sections))
    fixtures["multi_sheet.xlsx"].write_bytes(xlsx_bytes(_XLSX_SHEETS))
    fixtures["sample.png"].write_bytes(png_bytes("HP LaserJet Pro M404dn PRN-M404"))
    return fixtures


__all__ = [
    "PDF_SIGNATURE",
    "PNG_SIGNATURE",
    "docx_bytes",
    "generate_fixtures",
    "is_well_formed_ooxml",
    "is_well_formed_pdf",
    "is_well_formed_png",
    "pdf_bytes",
    "png_bytes",
    "xlsx_bytes",
]
