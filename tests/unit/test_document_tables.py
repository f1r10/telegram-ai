"""Phase 2 M4: PDF table detection and extraction inside the structure view.

Tables are recorded *inside* ``DocumentStructure`` (``tables[]``) and are read only from the
flag-gated per-page geometry sidecar, so the flat ``blocks`` list — the only thing chunking
consumes — stays byte-identical and a page's ``page_text`` block is never rewritten. Everything
here is deterministic and offline: no PDF is opened.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from office_assistant.processing.models import ContentBlock, ParsedDocument
from office_assistant.processing.structure import build_document_structure
from office_assistant.processing.worker_service import build_normalized_payload


def _line(text: str, x: float, y: float, size: float = 12.0) -> dict[str, Any]:
    return {"text": text, "x": x, "y": y, "size": size}


def _grid_rules(
    left: float,
    top: float,
    col_widths: tuple[float, ...],
    row_heights: tuple[float, ...],
    *,
    gap_columns: tuple[tuple[int, int], ...] = (),
) -> list[list[float]]:
    """The rule segments of a rectangular grid, minus the vertical edges listed in ``gap_columns``.

    ``gap_columns`` entries are ``(row_index, column_boundary)`` pairs whose vertical edge is left
    undrawn, which is how the fixture truth encodes a horizontal merge.
    """

    xs = [left]
    for width in col_widths:
        xs.append(xs[-1] + width)
    ys = [top]
    for height in row_heights:
        ys.append(ys[-1] - height)
    rules: list[list[float]] = []
    for y in ys:
        for index in range(len(xs) - 1):
            rules.append([xs[index], y, xs[index + 1], y])
    for boundary, x in enumerate(xs):
        for row in range(len(ys) - 1):
            if (row, boundary) in gap_columns:
                continue
            rules.append([x, ys[row], x, ys[row + 1]])
    return rules


def _grid_lines(
    left: float,
    top: float,
    col_widths: tuple[float, ...],
    row_heights: tuple[float, ...],
    rows: tuple[tuple[str, ...], ...],
) -> list[dict[str, Any]]:
    xs = [left]
    for width in col_widths:
        xs.append(xs[-1] + width)
    ys = [top]
    for height in row_heights:
        ys.append(ys[-1] - height)
    lines: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        for col_index, text in enumerate(row):
            if not text:
                continue
            lines.append(
                _line(text, xs[col_index] + 5.0, ys[row_index] - row_heights[row_index] + 5.0)
            )
    return lines


def _page(number: int, lines: list[dict[str, Any]], rules: Sequence[Any]) -> dict[str, Any]:
    return {"page": number, "width": 612.0, "height": 792.0, "lines": lines, "rules": list(rules)}


def _layout(pages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"pages": pages}


def _page_block(sequence: int, page: int, text: str) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "block_type": "page_text",
        "text": text,
        "locator": {"page": page},
    }


def _cell_texts(table: dict[str, Any]) -> dict[tuple[int, int], str]:
    return {(cell["row"], cell["col"]): cell["text"] for cell in table["cells"]}


# --- ruled tables --------------------------------------------------------------------------------


def test_a_bordered_grid_is_one_table_with_its_cells_at_the_right_positions() -> None:
    widths = (100.0, 100.0)
    heights = (20.0, 20.0, 20.0)
    rows = (("Ad", "Qiymet"), ("Alma", "2"), ("Armud", "3"))
    layout = _layout(
        [
            _page(
                1,
                _grid_lines(72.0, 700.0, widths, heights, rows),
                _grid_rules(72.0, 700.0, widths, heights),
            )
        ]
    )

    structure = build_document_structure([_page_block(1, 1, "Ad Qiymet Alma 2 Armud 3")], layout)

    assert len(structure.tables) == 1
    table = structure.tables[0]
    assert (table["rows"], table["columns"]) == (3, 2)
    assert table["detection"] == "ruled"
    assert table["locator"] == {"page": 1, "table_index": 1}
    assert table["source"] == {"format": "pdf", "page": 1, "table_index": 1}
    assert table["id"] == "pdf-page-1-table-1"
    assert table["text"] == "Ad | Qiymet\nAlma | 2\nArmud | 3"
    assert _cell_texts(table) == {
        (1, 1): "Ad",
        (1, 2): "Qiymet",
        (2, 1): "Alma",
        (2, 2): "2",
        (3, 1): "Armud",
        (3, 2): "3",
    }


def test_two_grids_on_one_page_stay_two_tables() -> None:
    widths = (100.0, 100.0)
    heights = (20.0, 20.0, 20.0)
    upper = (("A1", "A2"), ("A3", "A4"), ("A5", "A6"))
    lower = (("B1", "B2"), ("B3", "B4"), ("B5", "B6"))
    lines = [
        *_grid_lines(72.0, 700.0, widths, heights, upper),
        *_grid_lines(72.0, 400.0, widths, heights, lower),
    ]
    rules = [*_grid_rules(72.0, 700.0, widths, heights), *_grid_rules(72.0, 400.0, widths, heights)]

    structure = build_document_structure(
        [_page_block(1, 1, "x")], _layout([_page(1, lines, rules)])
    )

    assert [table["locator"] for table in structure.tables] == [
        {"page": 1, "table_index": 1},
        {"page": 1, "table_index": 2},
    ]
    assert _cell_texts(structure.tables[0])[(1, 1)] == "A1"
    assert _cell_texts(structure.tables[1])[(1, 1)] == "B1"


def test_a_table_across_pages_is_not_merged() -> None:
    widths = (100.0, 100.0)
    heights = (20.0, 20.0, 20.0)
    pages = [
        _page(
            1,
            _grid_lines(72.0, 700.0, widths, heights, (("A1", "A2"), ("A3", "A4"), ("A5", "A6"))),
            _grid_rules(72.0, 700.0, widths, heights),
        ),
        _page(
            2,
            _grid_lines(72.0, 700.0, widths, heights, (("B1", "B2"), ("B3", "B4"), ("B5", "B6"))),
            _grid_rules(72.0, 700.0, widths, heights),
        ),
    ]

    structure = build_document_structure(
        [_page_block(1, 1, "x"), _page_block(2, 2, "y")], _layout(pages)
    )

    assert [table["locator"] for table in structure.tables] == [
        {"page": 1, "table_index": 1},
        {"page": 2, "table_index": 1},
    ]
    assert _cell_texts(structure.tables[0])[(1, 1)] == "A1"
    assert _cell_texts(structure.tables[1])[(1, 1)] == "B1"


def test_an_undrawn_internal_edge_recovers_a_merged_cell() -> None:
    widths = (80.0, 80.0)
    heights = (20.0, 20.0)
    rows = (("Region",), ("Baki", "120"))
    lines = _grid_lines(100.0, 300.0, widths, heights, rows)
    rules = _grid_rules(100.0, 300.0, widths, heights, gap_columns=((0, 1),))

    structure = build_document_structure(
        [_page_block(1, 1, "Region Baki 120")], _layout([_page(1, lines, rules)])
    )

    table = structure.tables[0]
    assert (table["rows"], table["columns"]) == (2, 2)
    assert _cell_texts(table) == {(1, 1): "Region", (2, 1): "Baki", (2, 2): "120"}
    assert table["merged"] == [{"row": 1, "col": 1, "row_span": 1, "col_span": 2}]


def test_an_empty_cell_is_kept_with_an_empty_text() -> None:
    widths = (100.0, 100.0)
    heights = (20.0, 20.0, 20.0)
    rows = (("Kod", "Ad"), ("PRN", ""), ("", "kamera"))
    lines = _grid_lines(72.0, 700.0, widths, heights, rows)
    rules = _grid_rules(72.0, 700.0, widths, heights)

    structure = build_document_structure(
        [_page_block(1, 1, "x")], _layout([_page(1, lines, rules)])
    )

    table = structure.tables[0]
    assert len(table["cells"]) == 6
    assert _cell_texts(table)[(2, 2)] == ""
    assert _cell_texts(table)[(3, 1)] == ""


# --- borderless, prose and malformed input -------------------------------------------------------


def test_aligned_short_lines_without_rules_are_a_borderless_table() -> None:
    lines = [
        _line("Ad", 72.0, 300.0),
        _line("Qiymet", 200.0, 300.0),
        _line("Alma", 72.0, 280.0),
        _line("2", 200.0, 280.0),
        _line("Armud", 72.0, 260.0),
        _line("3", 200.0, 260.0),
    ]

    structure = build_document_structure([_page_block(1, 1, "x")], _layout([_page(1, lines, [])]))

    assert len(structure.tables) == 1
    assert structure.tables[0]["detection"] == "borderless"
    assert _cell_texts(structure.tables[0]) == {
        (1, 1): "Ad",
        (1, 2): "Qiymet",
        (2, 1): "Alma",
        (2, 2): "2",
        (3, 1): "Armud",
        (3, 2): "3",
    }


def test_two_columns_of_running_text_are_not_a_table() -> None:
    left = [
        "This agreement is concluded between the parties and remains in force",
        "until every obligation described below has been fully discharged",
        "and the final settlement report has been signed by both parties",
    ]
    right = [
        "Either party may terminate this agreement with thirty days of",
        "written notice delivered to the registered address, and the",
        "termination does not release any accrued payment obligation",
    ]
    lines = [
        *(_line(text, 72.0, 300.0 - index * 14.0) for index, text in enumerate(left)),
        *(_line(text, 330.0, 300.0 - index * 14.0) for index, text in enumerate(right)),
    ]

    structure = build_document_structure([_page_block(1, 1, "x")], _layout([_page(1, lines, [])]))

    assert structure.tables == []


def test_a_two_row_aligned_block_is_below_the_table_minimum() -> None:
    lines = [
        _line("Ad", 72.0, 300.0),
        _line("Qiymet", 200.0, 300.0),
        _line("Alma", 72.0, 280.0),
        _line("2", 200.0, 280.0),
    ]

    structure = build_document_structure([_page_block(1, 1, "x")], _layout([_page(1, lines, [])]))

    assert structure.tables == []


def test_a_malformed_rule_record_is_ignored_and_never_crashes() -> None:
    rules: list[Any] = [["bad"], [1, 2, 3, 4], "nope"]
    layout = _layout([_page(1, [_line("Ad", 72.0, 300.0)], rules)])

    structure = build_document_structure([_page_block(1, 1, "x")], layout)

    assert structure.tables == []


def test_a_page_with_no_lines_is_an_honest_miss() -> None:
    rules = _grid_rules(72.0, 700.0, (100.0, 100.0), (20.0, 20.0, 20.0))

    structure = build_document_structure([_page_block(1, 1, "")], _layout([_page(1, [], rules)]))

    assert structure.tables == []


def test_no_tables_without_a_layout_sidecar() -> None:
    structure = build_document_structure([_page_block(1, 1, "Ad Qiymet")])

    assert structure.tables == []


# --- compatibility ------------------------------------------------------------------------------


def test_pdf_tables_are_additive_and_the_flat_blocks_stay_identical() -> None:
    widths = (100.0, 100.0)
    heights = (20.0, 20.0, 20.0)
    rows = (("Ad", "Qiymet"), ("Alma", "2"), ("Armud", "3"))
    blocks = [_page_block(1, 1, "Ad Qiymet Alma 2 Armud 3")]
    layout = _layout(
        [
            _page(
                1,
                _grid_lines(72.0, 700.0, widths, heights, rows),
                _grid_rules(72.0, 700.0, widths, heights),
            )
        ]
    )

    structure = build_document_structure(blocks, layout)

    assert structure.blocks == blocks
    assert [block["block_type"] for block in structure.blocks] == ["page_text"]
    assert len(structure.tables) == 1


def test_flag_on_adds_tables_without_touching_the_flat_blocks() -> None:
    widths = (100.0, 100.0)
    heights = (20.0, 20.0, 20.0)
    rows = (("Ad", "Qiymet"), ("Alma", "2"), ("Armud", "3"))
    layout = _layout(
        [
            _page(
                1,
                _grid_lines(72.0, 700.0, widths, heights, rows),
                _grid_rules(72.0, 700.0, widths, heights),
            )
        ]
    )
    parsed = ParsedDocument(
        source_file_id=1,
        parser_name="pypdf",
        parser_version="6.19.0",
        media_type="application/pdf",
        metadata={"layout": layout},
        blocks=[ContentBlock(1, "page_text", "Ad Qiymet Alma 2 Armud 3", {"page": 1})],
    )

    payload = build_normalized_payload(parsed, [], document_intelligence_enabled=True)

    assert payload["blocks"] == parsed.as_dict()["blocks"]
    assert payload["structure"]["tables"][0]["rows"] == 3


def test_flag_off_ignores_the_table_sidecar_entirely() -> None:
    widths = (100.0, 100.0)
    heights = (20.0, 20.0, 20.0)
    rows = (("Ad", "Qiymet"), ("Alma", "2"), ("Armud", "3"))
    layout = _layout(
        [
            _page(
                1,
                _grid_lines(72.0, 700.0, widths, heights, rows),
                _grid_rules(72.0, 700.0, widths, heights),
            )
        ]
    )
    parsed = ParsedDocument(
        source_file_id=1,
        parser_name="pypdf",
        parser_version="6.19.0",
        media_type="application/pdf",
        metadata={"layout": layout},
        blocks=[ContentBlock(1, "page_text", "Ad Qiymet Alma 2 Armud 3", {"page": 1})],
    )

    payload = build_normalized_payload(parsed, [], document_intelligence_enabled=False)

    assert "structure" not in payload
    expected = parsed.as_dict()
    expected["session_items"] = []
    assert json.dumps(payload, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)


def test_table_view_is_deterministic() -> None:
    widths = (100.0, 100.0)
    heights = (20.0, 20.0, 20.0)
    rows = (("Ad", "Qiymet"), ("Alma", "2"), ("Armud", "3"))
    layout = _layout(
        [
            _page(
                1,
                _grid_lines(72.0, 700.0, widths, heights, rows),
                _grid_rules(72.0, 700.0, widths, heights),
            )
        ]
    )

    first = build_document_structure([_page_block(1, 1, "x")], layout).as_dict()
    second = build_document_structure([_page_block(1, 1, "x")], layout).as_dict()

    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
