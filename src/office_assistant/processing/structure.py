"""Deterministic structural view over the flat content blocks (Phase 2, Layer 3, gap G12).

This layer adds a *view* of the document a parser already produced. It re-extracts nothing,
re-orders nothing and rewrites no text: it groups the existing blocks into pages, sections,
tables and lists so a later phase can reason about structure without touching the flat
``blocks[]`` list that chunking consumes (ARCHITECTURE §6).

Compatibility: ``ParsedDocument.blocks`` stays the canonical representation, chunking is
unaffected because it reads only the flat ``blocks`` key, and ``stable_chunk_id`` is not
touched (it hashes the chunk locator, not the document). The normalizer is pure — the same
block list always yields the same structure — and it is only recorded on a parse run when
``gi_document_intelligence_enabled`` is on, so an opted-out deployment keeps the pre-Phase-2
artifact byte for byte.
"""

import re
from dataclasses import dataclass, field
from typing import Any, cast

HEADING_BLOCK_TYPES = frozenset({"heading"})
TABLE_BLOCK_TYPES = frozenset({"table_row"})

#: A leading ``1`` / ``1.`` / ``1.2`` / ``1.2.3`` marker. It is the only heading-level signal the
#: flat blocks carry today, because the DOCX outline level is not read until M6; a heading without
#: a numeric marker is recorded at level 1. See docs/OPEN-QUESTIONS.md Q56.
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)[.)]?(?:\s|$)")


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


def build_document_structure(blocks: list[dict[str, Any]]) -> DocumentStructure:
    """Derive the structure view from the flat, parser-produced content blocks."""

    return DocumentStructure(
        pages=_pages(blocks),
        sections=_sections(blocks),
        tables=_tables(blocks),
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


def _pages(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the blocks a page locator places, in first-seen (ascending) page order.

    ``reading_order`` is the order the parser produced for now; a geometric re-ordering is M3.
    ``size`` is left out because no parser records a page size yet, and ``furniture`` stays empty
    until M3 detects repeated bands.
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
    return pages


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

    PDF table extraction is M4 and is deliberately absent, so only the DOCX and XLSX row
    vocabularies are grouped here. Cell coordinates are 1-based row/column positions in the table.
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
