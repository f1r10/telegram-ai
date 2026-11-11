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
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import NamedTuple

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


def multi_page_pdf_bytes(pages: Sequence[Sequence[tuple[float, float, str]]]) -> bytes:
    """Build a multi-page PDF from positioned ``(x, y, text)`` fragments per page.

    Each page's fragments are written **in the given order**, so a fixture can reproduce the
    content-stream order a real PDF writer produced (including a wrong, interleaved one) and let the
    reading-order pass be measured against it. Coordinates are PDF points on a 612x792 Letter page
    with the origin at the bottom-left, so a larger ``y`` is nearer the top. Bytes stay
    deterministic (no dates, no compression, one shared base-14 Helvetica font).
    """

    font_id = 3
    page_ids = [4 + 2 * index for index in range(len(pages))]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii"),
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>".encode("ascii"),
    ]
    for index, fragments in enumerate(pages):
        content_id = 5 + 2 * index
        drawing = [
            f"BT /F1 12 Tf 1 0 0 1 {x:g} {y:g} Tm ({_escape(_ascii(text))}) Tj ET"
            for x, y, text in fragments
        ]
        content = "\n".join(drawing).encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode(
                "ascii"
            )
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
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
    out += (f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n").encode(
        "ascii"
    )
    return bytes(out)


class LayoutFixture(NamedTuple):
    """One generated layout fixture plus the truth the M3 metrics are scored against.

    ``page_order`` holds the correct reading order of each page's **non-furniture** lines;
    ``furniture`` holds the furniture keys (see
    :func:`office_assistant.processing.structure.furniture_key`) a detector is expected to find;
    ``lines`` holds every line the artifact must still carry, so a "no silent deletion" assertion
    has something to check against.
    """

    name: str
    path: Path
    page_order: tuple[tuple[str, ...], ...]
    furniture: frozenset[str]
    lines: tuple[str, ...]


@dataclass(frozen=True)
class _Layout:
    """The fragments (with geometry) and the correct reading order of one generated layout page."""

    fragments: list[tuple[float, float, str]] = field(default_factory=list)
    order: list[str] = field(default_factory=list)


_COLUMN_X = (72.0, 320.0)
_THREE_COLUMN_X = (72.0, 250.0, 440.0)
_COLUMN_TOP = 700.0
_LINE_STEP = 16.0
_HEADER_Y = 760.0
_FOOTER_Y = 40.0


def _columns(labels: Sequence[Sequence[str]], xs: Sequence[float]) -> _Layout:
    """Place each column's labels top-to-bottom, interleaving the fragment order row by row.

    Interleaving is the defect being measured: a content-stream-order reader emits ``L1 R1 L2 ...``
    while a reader must see ``L1 L2 ... R1 R2``.
    """

    layout = _Layout()
    rows = max(len(column) for column in labels)
    for row in range(rows):
        for column, xs_value in enumerate(xs):
            if row < len(labels[column]):
                layout.fragments.append(
                    (xs_value, _COLUMN_TOP - row * _LINE_STEP, labels[column][row])
                )
    for column in labels:
        layout.order.extend(column)
    return layout


def _two_column_contract() -> _Layout:
    return _columns(
        [["LEFT-1", "LEFT-2", "LEFT-3", "LEFT-4"], ["RIGHT-1", "RIGHT-2", "RIGHT-3", "RIGHT-4"]],
        _COLUMN_X,
    )


def _three_column_contract() -> _Layout:
    labels = [
        ["COL-A-1", "COL-A-2", "COL-A-3"],
        ["COL-B-1", "COL-B-2", "COL-B-3"],
        ["COL-C-1", "COL-C-2", "COL-C-3"],
    ]
    return _columns(labels, _THREE_COLUMN_X)


def _mixed_layout() -> _Layout:
    """A full-width title above two columns: the page must read title, left column, right column."""

    title = "MIXED LAYOUT TITLE"
    layout = _Layout(fragments=[(72.0, 744.0, title)], order=[title])
    columns = _columns([["L-1", "L-2", "L-3"], ["R-1", "R-2", "R-3"]], _COLUMN_X)
    layout.fragments.extend(columns.fragments)
    layout.order.extend(columns.order)
    return layout


def _different_page_layouts() -> list[_Layout]:
    """One document whose pages do not share a layout: page 1 single column, page 2 two columns."""

    single = _Layout(
        fragments=[
            (72.0, _COLUMN_TOP - index * _LINE_STEP, f"P1-LINE-{index + 1}") for index in range(3)
        ],
    )
    single.order.extend(f"P1-LINE-{index + 1}" for index in range(3))
    columns = _columns([["P2-LEFT-1", "P2-LEFT-2"], ["P2-RIGHT-1", "P2-RIGHT-2"]], _COLUMN_X)
    return [single, columns]


def _furniture_pages() -> list[_Layout]:
    """Three pages with a repeating header and a repeating-but-varying footer, plus unique bodies.

    ``order`` holds only the body lines: the header and the ``"Page N"`` footer repeat, so they are
    the furniture a detector must find and must *not* report as body reading-order lines.
    """

    pages: list[_Layout] = []
    for page_number in range(1, 4):
        layout = _Layout()
        layout.fragments.append((72.0, _HEADER_Y, "INTERNAL DOCUMENT"))
        for index in range(3):
            line = f"BODY-P{page_number}-{index + 1}"
            layout.fragments.append((72.0, _COLUMN_TOP - index * _LINE_STEP, line))
            layout.order.append(line)
        layout.fragments.append((72.0, _FOOTER_Y, f"Page {page_number}"))
        pages.append(layout)
    return pages


def _legitimate_repetition_pages() -> list[_Layout]:
    """Repeated **body** text (must be kept) with per-page-unique header and footer text.

    Nothing here repeats across pages as a *key*, so no line is furniture: the repeated clause and
    the per-page-unique header/footer are all ordinary content and all appear in the reading order.
    The header and footer vary by WORD, not by digit, because ``furniture_key`` masks digits (so
    ``"Page 3"`` and its neighbours collapse to one key) — a digit-varying line would be a true
    positive, not the false positive this fixture guards against.
    """

    repeated = "THIS CLAUSE APPLIES TO ALL AGREEMENTS"
    headers = ("ALPHA INTERNAL NOTICE", "BETA INTERNAL NOTICE", "GAMMA INTERNAL NOTICE")
    footers = ("REVISION ALPHA", "REVISION BETA", "REVISION GAMMA")
    pages: list[_Layout] = []
    for index in range(3):
        header = headers[index]
        first = f"UNIQUE-BODY-{index + 1}-1"
        second = f"UNIQUE-BODY-{index + 1}-2"
        footer = footers[index]
        layout = _Layout(
            fragments=[
                (72.0, _HEADER_Y, header),
                (72.0, 500.0, first),
                (72.0, 484.0, second),
                (72.0, 400.0, repeated),
                (72.0, _FOOTER_Y, footer),
            ],
            order=[header, first, second, repeated, footer],
        )
        pages.append(layout)
    return pages


def _all_lines(layouts: Sequence[_Layout]) -> tuple[str, ...]:
    return tuple(text for layout in layouts for _, _, text in layout.fragments)


def generate_layout_fixtures(directory: Path) -> dict[str, LayoutFixture]:
    """Write the M3 reading-order/furniture fixtures and return their truth.

    These are separate from :func:`generate_fixtures` on purpose: M0's fixture-set assertion and the
    binary-free guard both keep working unchanged (TEST_STRATEGY.md §2 — no existing test weakened).
    """

    directory.mkdir(parents=True, exist_ok=True)

    def write(name: str, layouts: Sequence[_Layout]) -> Path:
        path = directory / name
        path.write_bytes(multi_page_pdf_bytes([layout.fragments for layout in layouts]))
        return path

    two_column = _two_column_contract()
    three_column = _three_column_contract()
    mixed = _mixed_layout()
    different = _different_page_layouts()
    furniture = _furniture_pages()
    repetition = _legitimate_repetition_pages()

    return {
        "two_column_contract.pdf": LayoutFixture(
            "two_column_contract.pdf",
            write("two_column_contract.pdf", [two_column]),
            (tuple(two_column.order),),
            frozenset(),
            _all_lines([two_column]),
        ),
        "three_column_contract.pdf": LayoutFixture(
            "three_column_contract.pdf",
            write("three_column_contract.pdf", [three_column]),
            (tuple(three_column.order),),
            frozenset(),
            _all_lines([three_column]),
        ),
        "mixed_layout.pdf": LayoutFixture(
            "mixed_layout.pdf",
            write("mixed_layout.pdf", [mixed]),
            (tuple(mixed.order),),
            frozenset(),
            _all_lines([mixed]),
        ),
        "different_page_layouts.pdf": LayoutFixture(
            "different_page_layouts.pdf",
            write("different_page_layouts.pdf", different),
            tuple(tuple(layout.order) for layout in different),
            frozenset(),
            _all_lines(different),
        ),
        "furniture.pdf": LayoutFixture(
            "furniture.pdf",
            write("furniture.pdf", furniture),
            tuple(tuple(layout.order) for layout in furniture),
            frozenset({"internal document", "page #"}),
            _all_lines(furniture),
        ),
        "legitimate_repetition.pdf": LayoutFixture(
            "legitimate_repetition.pdf",
            write("legitimate_repetition.pdf", repetition),
            tuple(tuple(layout.order) for layout in repetition),
            frozenset(),
            _all_lines(repetition),
        ),
    }


# --- M4: PDF table fixtures ----------------------------------------------------------------------
#
# A table fixture needs two things the M3 builder cannot produce: non-ASCII text (Azerbaijani and
# Russian cells) and drawn rules (a bordered table). Both are generated here without a new
# dependency. The page uses a Type0 / Identity-H font whose ``ToUnicode`` map is the identity, so a
# two-byte code point written as a hex string extracts back to itself — ``Məhsul`` and ``Продукт``
# survive ``pypdf`` even though the base-14 fonts cannot carry them.

_TOUNICODE_CMAP = (
    b"/CIDInit /ProcSet findresource begin\n"
    b"12 dict begin\n"
    b"begincmap\n"
    b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
    b"/CMapName /Adobe-Identity-UCS def\n"
    b"/CMapType 2 def\n"
    b"1 begincodespacerange\n"
    b"<0000> <FFFF>\n"
    b"endcodespacerange\n"
    b"1 beginbfrange\n"
    b"<0000> <FFFF> <0000>\n"
    b"endbfrange\n"
    b"endcmap\n"
    b"CMapName currentdict /CMap defineresource pop\n"
    b"end\n"
    b"end\n"
)


class _PageDrawing(NamedTuple):
    """One generated page: positioned text fragments and drawn rule segments."""

    texts: tuple[tuple[float, float, str], ...] = ()
    horizontal: tuple[tuple[float, float, float], ...] = ()
    vertical: tuple[tuple[float, float, float], ...] = ()


def table_pdf_bytes(pages: Sequence[_PageDrawing]) -> bytes:
    """Build a multi-page PDF that can carry non-ASCII text and drawn rule segments.

    Every text fragment is its own ``BT ... ET`` text object so ``pypdf`` reports one visitor call
    per fragment with its own origin (a shared text object on one line is merged into a single
    call). A rule is a stroked ``m``/``l`` path; the content stream therefore contains no ``cm``
    transform, which is the assumption the rule capture makes. Bytes stay deterministic.
    """

    font_id = 3
    page_ids = [7 + 2 * index for index in range(len(pages))]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii"),
        b"<< /Type /Font /Subtype /Type0 /BaseFont /Helvetica /Encoding /Identity-H "
        b"/DescendantFonts [4 0 R] /ToUnicode 6 0 R >>",
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Helvetica "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        b"/FontDescriptor 5 0 R /DW 500 >>",
        b"<< /Type /FontDescriptor /FontName /Helvetica /Flags 4 /FontBBox [0 0 1000 1000] "
        b"/ItalicAngle 0 /Ascent 1000 /Descent 0 /CapHeight 1000 /StemV 80 >>",
        b"<< /Length %d >>\nstream\n" % len(_TOUNICODE_CMAP) + _TOUNICODE_CMAP + b"\nendstream",
    ]
    for index, drawing in enumerate(pages):
        content_id = 8 + 2 * index
        operations: list[str] = []
        for x, y, text in drawing.texts:
            hex_text = "".join(f"{ord(character):04X}" for character in text)
            operations.append(f"BT /F1 12 Tf 1 0 0 1 {x:g} {y:g} Tm <{hex_text}> Tj ET")
        for y, x0, x1 in drawing.horizontal:
            operations.append(f"{x0:g} {y:g} m {x1:g} {y:g} l S")
        for x, y0, y1 in drawing.vertical:
            operations.append(f"{x:g} {y0:g} m {x:g} {y1:g} l S")
        content = "\n".join(operations).encode("ascii")
        resources = (
            f"/Resources << /Font << /F1 {font_id} 0 R >> >>"
            if drawing.texts
            else "/Resources << >>"
        )
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"{resources} /Contents {content_id} 0 R >>".encode("ascii")
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    return _assemble_pdf(objects)


def _assemble_pdf(objects: Sequence[bytes]) -> bytes:
    """Write numbered objects plus a correct cross-reference table into a byte string."""

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
    out += (f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n").encode(
        "ascii"
    )
    return bytes(out)


class TableTruth(NamedTuple):
    """One table a fixture contains: its page, shape, ideal cell grid and merged regions."""

    page: int
    rows: int
    columns: int
    cells: dict[tuple[int, int], str]
    bordered: bool
    #: ``(row, col, row_span, col_span)`` merged regions, 1-based at the anchor cell.
    merged: tuple[tuple[int, int, int, int], ...] = ()
    #: ``[x0, y0, x1, y1]`` box of the whole table, so a detection can be matched to its truth.
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


class TableFixture(NamedTuple):
    """One generated table fixture plus the truth the M4 metrics are scored against."""

    name: str
    path: Path
    tables: tuple[TableTruth, ...]
    lines: tuple[str, ...]


@dataclass(frozen=True)
class _Grid:
    """A rectangular table placed on a generated page, its cells and its grid boundaries."""

    left: float
    top: float
    col_widths: tuple[float, ...]
    row_heights: tuple[float, ...]
    rows: tuple[tuple[str, ...], ...]
    bordered: bool = True
    merged: tuple[tuple[int, int, int, int], ...] = ()

    def _x_boundaries(self) -> list[float]:
        xs = [self.left]
        for width in self.col_widths:
            xs.append(xs[-1] + width)
        return xs

    def _y_boundaries(self) -> list[float]:
        ys = [self.top]
        for height in self.row_heights:
            ys.append(ys[-1] - height)
        return ys

    def _inside_merge_horizontal(self, row_boundary: int, column: int) -> bool:
        """True when the internal horizontal segment is absent inside a vertical merge."""

        for row0, col0, row_span, col_span in self.merged:
            rows_ok = row0 - 1 < row_boundary <= row0 - 1 + row_span - 1
            cols_ok = col0 - 1 <= column <= col0 - 1 + col_span - 1
            if rows_ok and cols_ok:
                return True
        return False

    def _inside_merge_vertical(self, row_index: int, column_boundary: int) -> bool:
        """True when the vertical boundary at ``column_boundary`` is absent inside a merge."""

        for row0, col0, row_span, col_span in self.merged:
            rows_ok = row0 - 1 <= row_index <= row0 - 1 + row_span - 1
            cols_ok = col0 - 1 < column_boundary <= col0 - 1 + col_span - 1
            if rows_ok and cols_ok:
                return True
        return False

    def _covered_cells(self) -> set[tuple[int, int]]:
        """Grid positions a merge covers (every position of a merged region except its anchor)."""

        covered: set[tuple[int, int]] = set()
        for row0, col0, row_span, col_span in self.merged:
            for extra_row in range(row_span):
                for extra_col in range(col_span):
                    if extra_row or extra_col:
                        covered.add((row0 - 1 + extra_row, col0 - 1 + extra_col))
        return covered

    def drawing(self) -> _PageDrawing:
        """The page fragments and rules that render this table."""

        xs = self._x_boundaries()
        ys = self._y_boundaries()
        rows = len(self.row_heights)
        columns = len(self.col_widths)
        texts: list[tuple[float, float, str]] = []
        covered = self._covered_cells()
        for row_index, row in enumerate(self.rows):
            for col_index in range(columns):
                if (row_index, col_index) in covered:
                    continue
                text = row[col_index] if col_index < len(row) else ""
                if not text:
                    continue
                x = xs[col_index] + _CELL_PADDING
                y = ys[row_index] - self.row_heights[row_index] + _CELL_PADDING
                texts.append((x, y, text))
        horizontal: list[tuple[float, float, float]] = []
        vertical: list[tuple[float, float, float]] = []
        if self.bordered:
            for boundary in range(rows + 1):
                y = ys[boundary]
                for column in range(columns):
                    if 0 < boundary < rows and self._inside_merge_horizontal(boundary, column):
                        continue
                    horizontal.append((y, xs[column], xs[column + 1]))
            for boundary in range(columns + 1):
                x = xs[boundary]
                for row_index in range(rows):
                    if 0 < boundary < columns and self._inside_merge_vertical(row_index, boundary):
                        continue
                    vertical.append((x, ys[row_index], ys[row_index + 1]))
        return _PageDrawing(tuple(texts), tuple(horizontal), tuple(vertical))

    def truth(self, page: int) -> TableTruth:
        """The ideal table: shape, one text per *anchor* cell (a merge covers the rest), and box."""

        rows = len(self.row_heights)
        columns = len(self.col_widths)
        covered = self._covered_cells()
        cells: dict[tuple[int, int], str] = {}
        for row_index in range(rows):
            row = self.rows[row_index] if row_index < len(self.rows) else ()
            for col_index in range(columns):
                if (row_index, col_index) in covered:
                    continue
                cells[(row_index + 1, col_index + 1)] = (
                    row[col_index] if col_index < len(row) else ""
                )
        xs = self._x_boundaries()
        ys = self._y_boundaries()
        bbox = (xs[0], ys[-1], xs[-1], ys[0])
        return TableTruth(page, rows, columns, cells, self.bordered, self.merged, bbox)


_CELL_PADDING = 6.0
_TABLE_ROW_HEIGHT = 22.0


def _bordered_table(left: float, top: float) -> _Grid:
    return _Grid(
        left=left,
        top=top,
        col_widths=(80.0, 180.0, 70.0, 90.0),
        row_heights=(_TABLE_ROW_HEIGHT,) * 3,
        rows=(
            ("Kod", "Ad", "Miqdar", "Qiymet"),
            ("PRN-M404", "HP LaserJet Pro M404dn", "4", "540"),
            ("CAM-2143", "Hikvision DS-2CD2143G2-I", "12", "180"),
        ),
        bordered=True,
    )


def _borderless_table(left: float, top: float) -> _Grid:
    return _Grid(
        left=left,
        top=top,
        col_widths=(80.0, 170.0, 70.0, 80.0),
        row_heights=(_TABLE_ROW_HEIGHT,) * 3,
        rows=(
            ("Kod", "Ad", "Say", "Qiymet"),
            ("PRN-M404", "HP M404dn printer", "4", "540"),
            ("CAM-2143", "Hikvision dome camera", "12", "180"),
        ),
        bordered=False,
    )


def _merged_table(left: float, top: float) -> _Grid:
    return _Grid(
        left=left,
        top=top,
        col_widths=(110.0, 110.0, 110.0),
        row_heights=(_TABLE_ROW_HEIGHT,) * 3,
        rows=(
            ("Region", "Cemi", ""),
            ("Baki", "120", ""),
            ("Gence", "240", ""),
        ),
        bordered=True,
        merged=((1, 2, 1, 2), (2, 3, 2, 1)),
    )


def _empty_cells_table(left: float, top: float) -> _Grid:
    return _Grid(
        left=left,
        top=top,
        col_widths=(110.0, 110.0, 90.0),
        row_heights=(_TABLE_ROW_HEIGHT,) * 3,
        rows=(
            ("Kod", "Ad", "Say"),
            ("PRN-M404", "", "4"),
            ("", "kamera", ""),
        ),
        bordered=True,
    )


def _azerbaijani_table(left: float, top: float) -> _Grid:
    return _Grid(
        left=left,
        top=top,
        col_widths=(120.0, 190.0, 70.0),
        row_heights=(_TABLE_ROW_HEIGHT,) * 3,
        rows=(
            ("Məhsul", "Təsvir", "Qiymət"),
            ("Şəkər", "Ağ şəkər çörəyi", "2"),
            ("Çörək", "Günəşli çörək", "1"),
        ),
        bordered=True,
    )


def _multilang_table(left: float, top: float) -> _Grid:
    return _Grid(
        left=left,
        top=top,
        col_widths=(120.0, 200.0, 90.0),
        row_heights=(_TABLE_ROW_HEIGHT,) * 3,
        rows=(
            ("Məhsul", "Описание", "Price"),
            ("Şəkər", "Сахар белый", "2"),
            ("Camera", "Камера купольная", "180"),
        ),
        bordered=False,
    )


def _continuation_table(left: float, top: float, rows: tuple[tuple[str, ...], ...]) -> _Grid:
    return _Grid(
        left=left,
        top=top,
        col_widths=(80.0, 180.0, 70.0, 90.0),
        row_heights=(_TABLE_ROW_HEIGHT,) * len(rows),
        rows=rows,
        bordered=True,
    )


def _unrelated_table(left: float, top: float) -> _Grid:
    return _Grid(
        left=left,
        top=top,
        col_widths=(150.0, 90.0, 90.0),
        row_heights=(_TABLE_ROW_HEIGHT,) * 3,
        rows=(
            ("Heyvan", "Say", "Yas"),
            ("Pişik", "3", "2"),
            ("İt", "1", "5"),
        ),
        bordered=True,
    )


def _prose_columns() -> _PageDrawing:
    """Two long-line text columns and a short three-column block: never a table."""

    long_left = (
        "This agreement is concluded between the parties and remains in force",
        "until every obligation described below has been fully discharged",
        "and the final settlement report has been signed by both parties",
    )
    long_right = (
        "Either party may terminate this agreement with thirty days of",
        "written notice delivered to the registered address, and the",
        "termination does not release any accrued payment obligation",
    )
    texts: list[tuple[float, float, str]] = []
    for index, line in enumerate(long_left):
        texts.append((72.0, 690.0 - index * 14.0, line))
    for index, line in enumerate(long_right):
        texts.append((330.0, 690.0 - index * 14.0, line))
    short = (("COL-A", "COL-B", "COL-C"), ("COL-D", "COL-E", "COL-F"))
    for row_index, row in enumerate(short):
        for col_index, text in enumerate(row):
            texts.append((72.0 + col_index * 160.0, 260.0 - row_index * 20.0, text))
    return _PageDrawing(tuple(texts), (), ())


def _scanned_table() -> _PageDrawing:
    """A rules-only page: a drawn grid with no extractable text (a scan stand-in)."""

    horizontal = tuple((y, 60.0, 540.0) for y in (700.0, 660.0, 620.0, 580.0))
    vertical = tuple((x, 580.0, 700.0) for x in (60.0, 300.0, 540.0))
    return _PageDrawing((), horizontal, vertical)


def _page_text_lines(drawing: _PageDrawing) -> tuple[str, ...]:
    return tuple(text for _x, _y, text in drawing.texts)


def generate_table_fixtures(directory: Path) -> dict[str, TableFixture]:
    """Write the M4 table fixtures and return their truth, keyed by file name."""

    directory.mkdir(parents=True, exist_ok=True)

    def write(name: str, pages: Sequence[_PageDrawing]) -> Path:
        path = directory / name
        path.write_bytes(table_pdf_bytes(pages))
        return path

    def single(name: str, grid: _Grid) -> TableFixture:
        drawing = grid.drawing()
        return TableFixture(
            name, write(name, [drawing]), (grid.truth(1),), _page_text_lines(drawing)
        )

    bordered = _bordered_table(72.0, 680.0)
    borderless = _borderless_table(72.0, 680.0)
    merged = _merged_table(72.0, 680.0)
    empty_cells = _empty_cells_table(72.0, 680.0)
    azerbaijani = _azerbaijani_table(72.0, 680.0)
    multilang = _multilang_table(72.0, 680.0)

    first = _bordered_table(72.0, 700.0)
    second = _unrelated_table(72.0, 420.0)
    multiple_drawing = _PageDrawing(
        tuple([*first.drawing().texts, *second.drawing().texts]),
        tuple([*first.drawing().horizontal, *second.drawing().horizontal]),
        tuple([*first.drawing().vertical, *second.drawing().vertical]),
    )

    page_one = _continuation_table(
        72.0,
        140.0,
        (("Kod", "Ad", "Miqdar", "Qiymet"), ("PRN-M404", "HP M404dn printer", "4", "540")),
    )
    page_two = _continuation_table(
        72.0,
        760.0,
        (("PRN-M404", "HP M404dn printer", "4", "540"), ("CAM-2143", "Dome camera", "12", "180")),
    )
    page_three = _unrelated_table(72.0, 760.0)
    continuation_pages = [
        page_one.drawing(),
        page_two.drawing(),
        page_three.drawing(),
    ]

    prose = _prose_columns()
    scanned = _scanned_table()

    return {
        "bordered_table.pdf": single("bordered_table.pdf", bordered),
        "borderless_table.pdf": single("borderless_table.pdf", borderless),
        "merged_cells.pdf": single("merged_cells.pdf", merged),
        "empty_cells.pdf": single("empty_cells.pdf", empty_cells),
        "azerbaijani_table.pdf": single("azerbaijani_table.pdf", azerbaijani),
        "multilang_table.pdf": single("multilang_table.pdf", multilang),
        "multiple_tables.pdf": TableFixture(
            "multiple_tables.pdf",
            write("multiple_tables.pdf", [multiple_drawing]),
            (first.truth(1), second.truth(1)),
            _page_text_lines(multiple_drawing),
        ),
        "multi_page_table.pdf": TableFixture(
            "multi_page_table.pdf",
            write("multi_page_table.pdf", continuation_pages),
            (page_one.truth(1), page_two.truth(2), page_three.truth(3)),
            tuple(text for drawing in continuation_pages for _x, _y, text in drawing.texts),
        ),
        "not_a_table.pdf": TableFixture(
            "not_a_table.pdf",
            write("not_a_table.pdf", [prose]),
            (),
            _page_text_lines(prose),
        ),
        "scanned_table.pdf": TableFixture(
            "scanned_table.pdf",
            write("scanned_table.pdf", [scanned]),
            (),
            _page_text_lines(scanned),
        ),
    }


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
    "LayoutFixture",
    "TableFixture",
    "TableTruth",
    "docx_bytes",
    "generate_fixtures",
    "generate_layout_fixtures",
    "generate_table_fixtures",
    "is_well_formed_ooxml",
    "is_well_formed_pdf",
    "is_well_formed_png",
    "multi_page_pdf_bytes",
    "pdf_bytes",
    "png_bytes",
    "table_pdf_bytes",
    "xlsx_bytes",
]
