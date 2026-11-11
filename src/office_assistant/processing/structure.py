"""Deterministic structural view over the flat content blocks (Phase 2, Layer 3, gap G12).

This layer adds a *view* of the document a parser already produced. It re-extracts nothing and
rewrites no text: it groups the existing blocks into pages, sections, tables and lists so a later
phase can reason about structure without touching the flat ``blocks[]`` list that chunking consumes
(ARCHITECTURE §6).

Phase 2 M3 adds a geometric reading order and page-furniture detection, both recorded **inside this
view** so the flat blocks stay byte-identical: ``page["lines"]`` holds a page's text lines in
geometric reading order (recovering the column order a content stream interleaves, audit gap G7)
and ``page["furniture"]`` names the repeated top/bottom-band lines and page numbers (gap G8). A
furniture line is a *view* only — its text stays in the page's block, so extracted content is never
discarded.

Compatibility: ``ParsedDocument.blocks`` stays the canonical representation, chunking is
unaffected because it reads only the flat ``blocks`` key, and ``stable_chunk_id`` is not
touched (it hashes the chunk locator, not the document). The normalizer is pure — the same
block list and layout always yield the same structure — and it is only recorded on a parse run when
``gi_document_intelligence_enabled`` is on, so an opted-out deployment keeps the pre-Phase-2
artifact byte for byte.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, cast

HEADING_BLOCK_TYPES = frozenset({"heading"})
TABLE_BLOCK_TYPES = frozenset({"table_row"})

#: A leading ``1`` / ``1.`` / ``1.2`` / ``1.2.3`` marker. It is the only heading-level signal the
#: flat blocks carry today, because the DOCX outline level is not read until M6; a heading without
#: a numeric marker is recorded at level 1. See docs/OPEN-QUESTIONS.md Q56.
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)[.)]?(?:\s|$)")

#: A run of digits, masked to ``#`` when a furniture key is built so a page number that changes from
#: page to page still repeats as one key (``"Page 1"`` and ``"Page 2"`` -> ``"page #"``).
_DIGITS = re.compile(r"\d+")

#: Fraction of a page's height at the top and bottom a repeating band must sit inside to count as
#: page furniture. ASSUMPTION: real headers/footers sit within ~8% of a page edge; a body line in
#: that band only becomes furniture once the same key repeats across pages (Q57).
_BAND_FRACTION = 0.08
#: A band line must appear on at least this many pages before it is furniture, so a one-page
#: document and a one-off mention are never flagged. ASSUMPTION (Q57).
_MIN_FURNITURE_PAGES = 2
#: Estimated advance width of one glyph as a fraction of the font size. A PDF text fragment records
#: its origin but not its measured width, so a column gutter is found from this estimate (Q57).
_WIDTH_PER_CHAR = 0.5
#: Font size assumed for a fragment that recorded none, so a line still has a usable box.
_DEFAULT_FONT_SIZE = 12.0


@dataclass(frozen=True, slots=True)
class DocumentStructure:
    """A JSON-serialisable structural view of one document's flat blocks.

    ``blocks`` mirrors the flat, ordered ``ContentBlock`` list unchanged; the other entries are
    additive relations over it. ``lists`` and ``relations`` are reserved by the design but stay
    empty in this milestone (list markers and cross-sheet edges arrive in M6/M7).
    """

    pages: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    sections: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    tables: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    lists: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    relations: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())
    blocks: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages": self.pages,
            "sections": self.sections,
            "tables": self.tables,
            "lists": self.lists,
            "relations": self.relations,
            "blocks": self.blocks,
        }


def build_document_structure(
    blocks: list[dict[str, Any]], layout: dict[str, Any] | None = None
) -> DocumentStructure:
    """Derive the structure view from the flat, parser-produced content blocks.

    ``layout`` is the optional per-page text geometry a PDF parser records (M3); when it is absent
    the page view falls back to the parser's own order and reports no furniture, exactly as it did
    before M3, and no PDF table is reported. PDF table extraction (M4) reads only that sidecar, so
    the flat ``blocks`` list and every existing table entry stay exactly as they were.
    """

    return DocumentStructure(
        pages=_pages(blocks, layout),
        sections=_sections(blocks),
        tables=[*_tables(blocks), *_pdf_tables(layout)],
        lists=[],
        relations=[],
        blocks=[dict(block) for block in blocks],
    )


def _locator(block: dict[str, Any]) -> dict[str, Any]:
    locator = block.get("locator")
    return cast("dict[str, Any]", locator) if isinstance(locator, dict) else {}


def _metadata(block: dict[str, Any]) -> dict[str, Any]:
    metadata = block.get("metadata")
    return cast("dict[str, Any]", metadata) if isinstance(metadata, dict) else {}


def _block_type(block: dict[str, Any]) -> str:
    return str(block.get("block_type") or "")


def _new_page(page: int) -> dict[str, Any]:
    return {"page": page, "reading_order": [], "furniture": []}


def _pages(
    blocks: list[dict[str, Any]], layout: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Group the blocks a page locator places, in first-seen (ascending) page order.

    ``reading_order`` is the block order the parser produced. When a page's text geometry is
    available, ``lines`` adds the page's text lines in geometric reading order and ``furniture``
    names the lines that repeat across pages. ``size`` is left out because no parser records a page
    size in the artifact itself.
    """

    pages: list[dict[str, Any]] = []
    index: dict[int, dict[str, Any]] = {}
    for block in blocks:
        page = _locator(block).get("page")
        if not isinstance(page, int) or isinstance(page, bool):
            continue
        group = index.get(page)
        if group is None:
            group = _new_page(page)
            index[page] = group
            pages.append(group)
        sequence = block.get("sequence")
        if isinstance(sequence, int) and not isinstance(sequence, bool):
            group["reading_order"].append(sequence)
    if layout is not None:
        _apply_layout(pages, layout)
    return pages


def furniture_key(text: str) -> str:
    """A page-furniture key: text case-folded and NFKC-normalised with every number masked.

    ``"Page 1"`` and ``"Page 2"`` share the key ``"page #"``, so a page number whose digits change
    from page to page still repeats as one key, while ``"Internal Document"`` becomes
    ``"internal document"``. Two lines are "the same" furniture exactly when their keys match.
    See docs/OPEN-QUESTIONS.md Q57.
    """

    normalized = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    return _DIGITS.sub("#", normalized)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _record(value: Any) -> dict[str, Any]:
    """A parser geometry record as a string-keyed mapping, or an empty mapping otherwise."""

    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _line(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One usable text line from a parser's geometry record, or ``None`` when it is unusable."""

    text = str(raw.get("text") or "").strip()
    if not text:
        return None
    size = _number(raw.get("size"))
    return {
        "text": text,
        "x": _number(raw.get("x")),
        "y": _number(raw.get("y")),
        "size": size if size > 0 else _DEFAULT_FONT_SIZE,
    }


def _band_edges(height: float) -> tuple[float, float]:
    """The top and bottom ``y`` a line must reach to sit in a furniture band of a page."""

    return height * (1.0 - _BAND_FRACTION), height * _BAND_FRACTION


def _apply_layout(pages: list[dict[str, Any]], layout: dict[str, Any]) -> None:
    """Add the geometric reading order and furniture view to every page that has geometry."""

    by_number: dict[int, list[dict[str, Any]]] = {}
    heights: dict[int, float] = {}
    raw_pages = layout.get("pages")
    if not isinstance(raw_pages, list):
        return
    for raw_page in cast("list[Any]", raw_pages):
        page_record = _record(raw_page)
        number = page_record.get("page")
        raw_lines = page_record.get("lines")
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        if not isinstance(raw_lines, list):
            continue
        records = [_record(item) for item in cast("list[Any]", raw_lines)]
        lines = [line for line in (_line(item) for item in records) if line is not None]
        if not lines:
            continue
        by_number[number] = lines
        heights[number] = _number(page_record.get("height"))
    if not by_number:
        return
    repeated = _repeated_furniture(by_number, heights)
    for page in pages:
        number = page.get("page")
        lines = by_number.get(number) if isinstance(number, int) else None
        if lines is None:
            continue
        body: list[dict[str, Any]] = []
        furniture: dict[str, dict[str, Any]] = {}
        for line in _reading_order(lines):
            key = furniture_key(line["text"])
            repeated_on = repeated.get(key)
            if repeated_on is None:
                body.append({"text": line["text"], "x": line["x"], "y": line["y"]})
            elif key not in furniture:
                furniture[key] = {
                    "key": key,
                    "text": line["text"],
                    "locator": {"page": number},
                    "repeated_on": repeated_on,
                }
        page["lines"] = body
        if furniture:
            page["furniture"] = [furniture[key] for key in sorted(furniture)]


def _repeated_furniture(
    by_number: dict[int, list[dict[str, Any]]], heights: dict[int, float]
) -> dict[str, list[int]]:
    """Furniture keys seen in a page's top or bottom band on at least two pages."""

    seen: dict[str, set[int]] = {}
    for number, lines in by_number.items():
        height = heights.get(number, 0.0)
        if height <= 0:
            continue
        top, bottom = _band_edges(height)
        for line in lines:
            if line["y"] >= top or line["y"] <= bottom:
                seen.setdefault(furniture_key(line["text"]), set()).add(number)
    return {
        key: sorted(numbers)
        for key, numbers in seen.items()
        if len(numbers) >= _MIN_FURNITURE_PAGES
    }


def _box(line: dict[str, Any]) -> dict[str, Any]:
    """A line's bounding box, its width estimated from the text and the font size."""

    size = line["size"]
    width = max(len(line["text"]) * size * _WIDTH_PER_CHAR, size)
    return {
        "line": line,
        "left": line["x"],
        "right": line["x"] + width,
        "bottom": line["y"],
        "top": line["y"] + size,
    }


def _largest_gap(intervals: list[tuple[float, float]]) -> tuple[float, float] | None:
    """The widest uncovered ``(gap, split_position)`` between merged intervals, or ``None``."""

    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    best: tuple[float, float] | None = None
    for lower, upper in zip(merged, merged[1:], strict=False):
        gap = upper[0] - lower[1]
        if gap > 0 and (best is None or gap > best[0]):
            best = (gap, (lower[1] + upper[0]) / 2.0)
    return best


def _reading_order(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover a page's reading order with a recursive XY-cut over the line boxes.

    At each step the widest whitespace gap — vertical (a column gutter) or horizontal (a band
    boundary) — splits the region; the upper/left region is read first and each part recurses. This
    is what turns an interleaved content stream (``L1 R1 L2 ...``) back into column order
    (``L1 L2 ... R1 R2 ...``) and keeps a full-width title above the columns it spans. When no gap
    remains (touching boxes) the region is read top-to-bottom then left-to-right.
    """

    return [box["line"] for box in _xy_cut([_box(line) for line in lines])]


def _xy_cut(boxes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(boxes) <= 1:
        return list(boxes)
    vertical = _largest_gap([(box["left"], box["right"]) for box in boxes])
    horizontal = _largest_gap([(box["bottom"], box["top"]) for box in boxes])
    if horizontal is not None and (vertical is None or horizontal[0] > vertical[0]):
        split = horizontal[1]
        above = [box for box in boxes if box["bottom"] >= split]
        below = [box for box in boxes if box["bottom"] < split]
        return _xy_cut(above) + _xy_cut(below)
    if vertical is not None:
        split = vertical[1]
        left = [box for box in boxes if box["left"] < split]
        right = [box for box in boxes if box["left"] >= split]
        return _xy_cut(left) + _xy_cut(right)
    return sorted(boxes, key=lambda box: (-box["bottom"], box["left"]))


def _heading_level(text: str) -> int:
    match = _NUMBERED_HEADING.match(text)
    if match is None:
        return 1
    return match.group(1).count(".") + 1


def _sections(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the heading hierarchy and the flat block range each section owns.

    A section covers its heading and every following block up to (but excluding) the next heading
    of the same or a higher level. The heading locator is carried verbatim for provenance.
    """

    headings = [
        (position, block)
        for position, block in enumerate(blocks)
        if _block_type(block) in HEADING_BLOCK_TYPES and str(block.get("text") or "").strip()
    ]
    last_position = len(blocks) - 1
    sections: list[dict[str, Any]] = []
    for order, (position, block) in enumerate(headings):
        text = str(block["text"]).strip()
        level = _heading_level(text)
        end_position = last_position
        for following_position, following in headings[order + 1 :]:
            if _heading_level(str(following["text"]).strip()) <= level:
                end_position = following_position - 1
                break
        if end_position < position:
            end_position = position
        start = block.get("sequence")
        end = blocks[end_position].get("sequence")
        section: dict[str, Any] = {
            "level": level,
            "title": text,
            "heading_sequence": start,
            "block_range": [start, end],
        }
        locator = _locator(block)
        if locator:
            section["locator"] = dict(locator)
        sections.append(section)
    return sections


def _row_cells(block: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    """The cell texts of one table row, preferring the parser's own cell values."""

    raw = metadata.get("cells")
    if not isinstance(raw, list):
        raw = metadata.get("values")
    if isinstance(raw, list):
        return ["" if value is None else str(value) for value in cast("list[Any]", raw)]
    text = str(block.get("text") or "")
    return text.split(" | ") if text else []


def _range_row(locator: dict[str, Any]) -> int | None:
    """The first spreadsheet row a ``range`` locator names (``"A42:C42"`` -> ``42``)."""

    cells_range = locator.get("range")
    if not isinstance(cells_range, str):
        return None
    head = cells_range.split(":", 1)[0]
    digits = "".join(char for char in head if char.isdigit())
    return int(digits) if digits else None


def _append_row(table: dict[str, Any], row_number: int | None, cells: list[str]) -> None:
    table["rows"] += 1
    table["columns"] = max(table["columns"], len(cells))
    row = row_number if row_number is not None else table["rows"]
    for column, text in enumerate(cells, start=1):
        table["cells"].append({"row": row, "col": column, "text": text})


def _tables(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group table rows into tables: one per DOCX ``table_index``, one per spreadsheet sheet.

    PDF tables are extracted from page geometry by :func:`_pdf_tables` (M4) and are appended after
    these, so this function still only reads the DOCX and XLSX row vocabularies. Cell coordinates
    are 1-based row/column positions in the table.
    """

    tables: list[dict[str, Any]] = []
    by_docx_table: dict[int, dict[str, Any]] = {}
    by_sheet: dict[str, dict[str, Any]] = {}
    for block in blocks:
        if _block_type(block) not in TABLE_BLOCK_TYPES:
            continue
        locator = _locator(block)
        table_index = locator.get("table_index")
        sheet = locator.get("sheet")
        if isinstance(table_index, int) and not isinstance(table_index, bool):
            table = by_docx_table.get(table_index)
            if table is None:
                table = _new_table(
                    f"docx-table-{table_index}",
                    {"format": "docx", "table_index": table_index},
                    {"table_index": table_index},
                )
                by_docx_table[table_index] = table
                tables.append(table)
            row_number = locator.get("row")
            row = (
                row_number
                if isinstance(row_number, int) and not isinstance(row_number, bool)
                else None
            )
            _append_row(table, row, _row_cells(block, _metadata(block)))
        elif isinstance(sheet, str):
            table = by_sheet.get(sheet)
            if table is None:
                table = _new_table(
                    f"xlsx-sheet-{sheet}",
                    {"format": "xlsx", "sheet": sheet},
                    {"sheet": sheet},
                )
                by_sheet[sheet] = table
                tables.append(table)
            _append_row(table, _range_row(locator), _row_cells(block, _metadata(block)))
    return tables


def _new_table(table_id: str, source: dict[str, Any], locator: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": table_id,
        "source": source,
        "locator": locator,
        "rows": 0,
        "columns": 0,
        "cells": [],
    }


# --- M4: PDF table extraction --------------------------------------------------------------------

#: Two grid lines within this distance (PDF points) are the same line. ASSUMPTION (Q58).
_GRID_TOLERANCE = 2.0
#: Fragments whose baselines are within this distance belong to one table row. ASSUMPTION (Q58).
_ROW_TOLERANCE = 4.0
#: Fragments whose left edges are within this distance belong to one table column. ASSUMPTION (Q58).
_COLUMN_TOLERANCE = 6.0
#: A borderless candidate needs at least this many rows and columns to be a table rather than a
#: short aligned list (a two-row, two-cell block is not a table). ASSUMPTION (Q58).
_MIN_TABLE_ROWS = 3
_MIN_TABLE_COLUMNS = 2
#: A borderless "cell" longer than this many characters is prose, not a table cell: this is what
#: keeps two columns of running text from being read as a borderless table. ASSUMPTION (Q58).
_MAX_CELL_CHARS = 40


def _segment(value: Any) -> tuple[float, float, float, float] | None:
    """One parser rule record as ``(x0, y0, x1, y1)``, or ``None`` when it is malformed."""

    if not isinstance(value, list) or len(cast("list[Any]", value)) != 4:
        return None
    parts: list[float] = []
    for part in cast("list[Any]", value):
        if isinstance(part, bool) or not isinstance(part, (int, float)):
            return None
        parts.append(float(part))
    return (parts[0], parts[1], parts[2], parts[3])


def _rules(record: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    """The well-formed rule segments of one page record; a malformed entry is ignored, not fatal."""

    raw = record.get("rules")
    if not isinstance(raw, list):
        return []
    return [
        segment
        for segment in (_segment(item) for item in cast("list[Any]", raw))
        if segment is not None
    ]


def _cluster(values: list[float], tolerance: float, *, reverse: bool = False) -> list[float]:
    """Distinct values merged where neighbours fall within ``tolerance``; ordered as requested."""

    centers: list[float] = []
    for value in sorted(set(values), reverse=reverse):
        if not centers or abs(centers[-1] - value) > tolerance:
            centers.append(value)
    return centers


def _line_origin(
    line: dict[str, Any],
    row_bounds: list[tuple[float, float]],
    col_bounds: list[tuple[float, float]],
) -> tuple[int, int] | None:
    """The ``(row, column)`` whose bounds contain a line's origin, or ``None`` outside the grid."""

    x = line["x"]
    y = line["y"]
    for row_index, (bottom, top) in enumerate(row_bounds):
        if bottom <= y <= top:
            for col_index, (left, right) in enumerate(col_bounds):
                if left <= x <= right:
                    return row_index, col_index
    return None


def _render_grid(rows: int, columns: int, texts: dict[tuple[int, int], str]) -> str:
    """A deterministic text rendering of the cell grid (the table block's ``text``)."""

    return "\n".join(
        " | ".join(texts.get((row, column), "") for column in range(1, columns + 1))
        for row in range(1, rows + 1)
    )


def _assemble_table(
    lines: list[dict[str, Any]],
    row_bounds: list[tuple[float, float]],
    col_bounds: list[tuple[float, float]],
    roots: dict[tuple[int, int], tuple[int, int]],
    detection: str,
) -> dict[str, Any]:
    """Build one table record from a grid, its merged-cell roots and the lines on the page."""

    rows = len(row_bounds)
    columns = len(col_bounds)
    components: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for row in range(rows):
        for column in range(columns):
            components.setdefault(roots[(row, column)], []).append((row, column))
    cells: list[dict[str, Any]] = []
    merged: list[dict[str, Any]] = []
    texts: dict[tuple[int, int], str] = {}
    for root in sorted(components):
        positions = components[root]
        row_min = min(row for row, _column in positions)
        row_max = max(row for row, _column in positions)
        col_min = min(column for _row, column in positions)
        col_max = max(column for _row, column in positions)
        collected: list[tuple[float, float, str]] = []
        for line in lines:
            origin = _line_origin(line, row_bounds, col_bounds)
            if origin is not None and roots[origin] == root:
                collected.append((line["y"], line["x"], line["text"]))
        collected.sort(key=lambda item: (-item[0], item[1]))
        text = " ".join(item[2] for item in collected)
        left = col_bounds[col_min][0]
        right = col_bounds[col_max][1]
        bottom = row_bounds[row_max][0]
        top = row_bounds[row_min][1]
        cells.append(
            {
                "row": row_min + 1,
                "col": col_min + 1,
                "text": text,
                "bbox": [round(left, 2), round(bottom, 2), round(right, 2), round(top, 2)],
            }
        )
        texts[(row_min + 1, col_min + 1)] = text
        row_span = row_max - row_min + 1
        col_span = col_max - col_min + 1
        if row_span > 1 or col_span > 1:
            merged.append(
                {
                    "row": row_min + 1,
                    "col": col_min + 1,
                    "row_span": row_span,
                    "col_span": col_span,
                }
            )
    return {
        "id": "",
        "source": {},
        "locator": {},
        "rows": rows,
        "columns": columns,
        "detection": detection,
        "text": _render_grid(rows, columns, texts),
        "bbox": [
            round(col_bounds[0][0], 2),
            round(row_bounds[-1][0], 2),
            round(col_bounds[-1][1], 2),
            round(row_bounds[0][1], 2),
        ],
        "cells": cells,
        "merged": merged,
    }


def _covers(
    segments: list[tuple[float, float, float]], fixed: float, low: float, high: float
) -> bool:
    """Whether a rule at ``fixed`` spans ``[low, high]`` within the grid tolerance."""

    return any(
        abs(position - fixed) <= _GRID_TOLERANCE
        and start <= low + _GRID_TOLERANCE
        and high <= end + _GRID_TOLERANCE
        for position, start, end in segments
    )


def _ruled_table(
    lines: list[dict[str, Any]], rules: list[tuple[float, float, float, float]]
) -> dict[str, Any] | None:
    """Recover a bordered table's grid and merged regions from the rules a page draws."""

    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []
    for x0, y0, x1, y1 in rules:
        if abs(y1 - y0) <= abs(x1 - x0):
            horizontal.append((round((y0 + y1) / 2.0, 2), min(x0, x1), max(x0, x1)))
        else:
            vertical.append((round((x0 + x1) / 2.0, 2), min(y0, y1), max(y0, y1)))
    row_lines = _cluster([y for y, _x0, _x1 in horizontal], _GRID_TOLERANCE, reverse=True)
    col_lines = _cluster([x for x, _y0, _y1 in vertical], _GRID_TOLERANCE)
    if len(row_lines) < 2 or len(col_lines) < 2:
        return None
    rows = len(row_lines) - 1
    columns = len(col_lines) - 1

    def row_edge(boundary: int, column: int) -> bool:
        return _covers(horizontal, row_lines[boundary], col_lines[column], col_lines[column + 1])

    def column_edge(row_index: int, boundary: int) -> bool:
        return _covers(
            vertical, col_lines[boundary], row_lines[row_index + 1], row_lines[row_index]
        )

    parent: dict[tuple[int, int], tuple[int, int]] = {
        (row, column): (row, column) for row in range(rows) for column in range(columns)
    }

    def find(cell: tuple[int, int]) -> tuple[int, int]:
        root = cell
        while parent[root] != root:
            root = parent[root]
        while parent[cell] != root:
            parent[cell], cell = root, parent[cell]
        return root

    def union(first: tuple[int, int], second: tuple[int, int]) -> None:
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parent[max(first_root, second_root)] = min(first_root, second_root)

    for row in range(rows):
        for column in range(columns - 1):
            if not column_edge(row, column + 1):
                union((row, column), (row, column + 1))
    for row in range(rows - 1):
        for column in range(columns):
            if not row_edge(row + 1, column):
                union((row, column), (row + 1, column))

    row_bounds = [(row_lines[index + 1], row_lines[index]) for index in range(rows)]
    col_bounds = [(col_lines[index], col_lines[index + 1]) for index in range(columns)]
    roots = {cell: find(cell) for cell in parent}
    return _assemble_table(lines, row_bounds, col_bounds, roots, "ruled")


def _row_groups(lines: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group a page's lines into rows by baseline, top row first, each row left-to-right."""

    ordered = sorted(lines, key=lambda line: (-line["y"], line["x"]))
    groups: list[list[dict[str, Any]]] = []
    for line in ordered:
        if groups and abs(groups[-1][0]["y"] - line["y"]) <= _ROW_TOLERANCE:
            groups[-1].append(line)
        else:
            groups.append([line])
    return groups


def _borderless_table(lines: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Recover a table with no rules from consistent, short, multi-cell rows.

    Ordinary two-column prose is *not* a table: its "cells" are whole sentences, so a row whose
    cells are longer than :data:`_MAX_CELL_CHARS` is prose and is discarded before a shape is
    attempted, and a two-row aligned block is below :data:`_MIN_TABLE_ROWS`. The residual risk is
    recorded, not hidden (Q58).
    """

    groups = _row_groups(lines)
    candidates = [
        group
        for group in groups
        if len(group) >= _MIN_TABLE_COLUMNS
        and all(len(line["text"]) <= _MAX_CELL_CHARS for line in group)
    ]
    if len(candidates) < _MIN_TABLE_ROWS:
        return None
    counts = {len(group) for group in candidates}
    if len(counts) != 1:
        return None
    columns = counts.pop()
    col_x = _cluster([line["x"] for group in candidates for line in group], _COLUMN_TOLERANCE)
    if len(col_x) != columns:
        return None
    row_bounds: list[tuple[float, float]] = []
    for group in candidates:
        sizes = [line["size"] for line in group]
        top = max(line["y"] + line["size"] for line in group)
        bottom = min(line["y"] for line in group) - min(sizes) * 0.5
        row_bounds.append((bottom, top))
    # A cell spans from the midpoint to its neighbour to the next midpoint; the outer edges are
    # extended by half an interior gap so the first and last cells still contain their text.
    edges = [col_x[0]]
    for index in range(1, len(col_x)):
        edges.append((col_x[index - 1] + col_x[index]) / 2.0)
    edges.append(2.0 * col_x[-1] - edges[-1])
    col_bounds = [(edges[index], edges[index + 1]) for index in range(columns)]
    roots = {
        (row, column): (row, column) for row in range(len(row_bounds)) for column in range(columns)
    }
    return _assemble_table(lines, row_bounds, col_bounds, roots, "borderless")


def _touching(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> bool:
    """Whether two axis-aligned rule segments meet, within the grid tolerance.

    A horizontal rule and the vertical rules it joins share an endpoint, so their boxes overlap once
    each is grown by :data:`_GRID_TOLERANCE`; two tables a page apart never overlap. This is what
    keeps two tables on one page from being read as one (Requirement 5).
    """

    ax0, ay0, ax1, ay1 = first
    bx0, by0, bx1, by1 = second
    return (
        min(ax0, ax1) - _GRID_TOLERANCE <= max(bx0, bx1)
        and min(bx0, bx1) - _GRID_TOLERANCE <= max(ax0, ax1)
        and min(ay0, ay1) - _GRID_TOLERANCE <= max(by0, by1)
        and min(by0, by1) - _GRID_TOLERANCE <= max(ay0, ay1)
    )


def _segment_groups(
    segments: list[tuple[float, float, float, float]],
) -> list[list[tuple[float, float, float, float]]]:
    """The touching groups a page's rules fall into: one bordered grid each.

    A page can draw several separate tables (or an unrelated box beside one); grouping the rules
    first means each grid is measured on its own, and a table is never extended by another's lines.
    ASSUMPTION: two tables that share a drawn border line are one table (Q58).
    """

    groups: list[list[tuple[float, float, float, float]]] = []
    for segment in segments:
        joined = [group for group in groups if any(_touching(segment, member) for member in group)]
        merged = [member for group in joined for member in group]
        merged.append(segment)
        groups = [group for group in groups if group not in joined]
        groups.append(merged)
    return groups


def _page_tables(
    lines: list[dict[str, Any]], rules: list[tuple[float, float, float, float]]
) -> list[dict[str, Any]]:
    """Every table one page holds, in top-to-bottom then left-to-right order.

    A ruled grid is preferred: each touching rule group becomes one table, so two grids on one page
    stay two tables. Only when a page draws no usable grid does the borderless heuristic run, and it
    produces at most one table. A page whose geometry is only stray rules yields nothing.
    """

    ruled = [
        table
        for table in (_ruled_table(lines, group) for group in _segment_groups(rules))
        if table is not None
    ]
    if ruled:
        ruled.sort(key=lambda table: (-table["bbox"][3], table["bbox"][0]))
        return ruled
    borderless = _borderless_table(lines)
    return [borderless] if borderless is not None else []


def _pdf_tables(layout: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract PDF tables from the per-page geometry sidecar (Phase 2 M4).

    Tables are detected per page and never merged across pages, so a table that continues onto the
    next page stays two tables with two locators rather than one silently joined table. Each page's
    tables are numbered ``table_index`` 1..k in reading position. A page with no geometry (no
    extracted text, e.g. a scan) yields nothing — an honest miss, not a guess.
    """

    if layout is None:
        return []
    raw_pages = layout.get("pages")
    if not isinstance(raw_pages, list):
        return []
    tables: list[dict[str, Any]] = []
    for raw_page in cast("list[Any]", raw_pages):
        record = _record(raw_page)
        number = record.get("page")
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        raw_lines = record.get("lines")
        if not isinstance(raw_lines, list):
            continue
        lines = [
            line
            for line in (_line(_record(item)) for item in cast("list[Any]", raw_lines))
            if line is not None
        ]
        if not lines:
            continue
        for index, table in enumerate(_page_tables(lines, _rules(record)), start=1):
            table["id"] = f"pdf-page-{number}-table-{index}"
            table["source"] = {"format": "pdf", "page": number, "table_index": index}
            table["locator"] = {"page": number, "table_index": index}
            tables.append(table)
    return tables
