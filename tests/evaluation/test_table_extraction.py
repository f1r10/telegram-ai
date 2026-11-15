"""M4: measured PDF table detection and extraction on generated fixtures.

Every number here comes from a real run: the generated PDF is parsed by the shipped ``PdfParser``
with geometry capture, the structure layer draws the tables from that sidecar, and the M0/M4
scorers compare the result with the fixture truth. TEST_STRATEGY.md §5 makes these report-only (no
accuracy threshold), so the assertions state the *measured* value rather than a target. The hard
gates — the flat ``blocks`` list is untouched and a table-less PDF produces no table — are asserted
here as well. No fixture binary is committed (risk R7).
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from office_assistant.processing.parsers import PdfParser
from office_assistant.processing.structure import build_document_structure

from .fixtures import TableFixture, generate_table_fixtures
from .metrics import score_table_extraction

_TABLE_NAMES = (
    "bordered_table.pdf",
    "borderless_table.pdf",
    "merged_cells.pdf",
    "empty_cells.pdf",
    "azerbaijani_table.pdf",
    "multilang_table.pdf",
    "multiple_tables.pdf",
    "multi_page_table.pdf",
    "not_a_table.pdf",
    "scanned_table.pdf",
)
_RULED_NAMES = (
    "bordered_table.pdf",
    "merged_cells.pdf",
    "empty_cells.pdf",
    "azerbaijani_table.pdf",
)


@pytest.fixture(scope="module")
def table_fixtures(tmp_path_factory: pytest.TempPathFactory) -> dict[str, TableFixture]:
    return generate_table_fixtures(tmp_path_factory.mktemp("m4-tables"))


def _parse(fixture: TableFixture) -> Any:
    return PdfParser(capture_layout=True).parse(
        fixture.path, source_file_id=0, media_type="application/pdf"
    )


def _capture(fixture: TableFixture) -> dict[str, Any]:
    """Parse a fixture with geometry capture and return the derived structure view."""

    parsed = _parse(fixture)
    layout = cast("dict[str, Any] | None", parsed.metadata.get("layout"))
    return build_document_structure(parsed.as_dict()["blocks"], layout).as_dict()


def _truth(fixture: TableFixture) -> list[dict[str, Any]]:
    return [truth._asdict() for truth in fixture.tables]


def _score(fixture: TableFixture) -> dict[str, int | float | None]:
    return score_table_extraction(_truth(fixture), _capture(fixture)["tables"])


# --- detection and cell accuracy -----------------------------------------------------------------


@pytest.mark.parametrize("name", _RULED_NAMES)
def test_ruled_table_fixtures_are_detected_with_exact_cells(
    name: str, table_fixtures: dict[str, TableFixture]
) -> None:
    score = _score(table_fixtures[name])

    assert score["detection_f1"] == 1.0, (name, score)
    assert score["false_positives"] == 0, (name, score)
    assert score["shape_accuracy"] == 1.0, (name, score)
    assert score["cell_f1"] == 1.0, (name, score)
    assert score["cell_text_accuracy"] == 1.0, (name, score)
    assert score["locator_preservation"] == 1.0, (name, score)


@pytest.mark.parametrize("name", ["borderless_table.pdf", "multilang_table.pdf"])
def test_borderless_table_fixtures_are_detected_from_aligned_lines(
    name: str, table_fixtures: dict[str, TableFixture]
) -> None:
    score = _score(table_fixtures[name])

    assert score["detection_f1"] == 1.0, (name, score)
    assert score["shape_accuracy"] == 1.0, (name, score)
    assert score["cell_f1"] == 1.0, (name, score)
    assert score["cell_text_accuracy"] == 1.0, (name, score)


def test_azerbaijani_and_mixed_language_cell_text_is_exact(
    table_fixtures: dict[str, TableFixture],
) -> None:
    azerbaijani = _capture(table_fixtures["azerbaijani_table.pdf"])["tables"][0]
    multilang = _capture(table_fixtures["multilang_table.pdf"])["tables"][0]

    az_cells = {(cell["row"], cell["col"]): cell["text"] for cell in azerbaijani["cells"]}
    mixed_cells = {(cell["row"], cell["col"]): cell["text"] for cell in multilang["cells"]}
    assert az_cells[(1, 1)] == "Məhsul" and az_cells[(2, 1)] == "Şəkər"
    assert az_cells[(3, 1)] == "Çörək" and az_cells[(2, 2)] == "Ağ şəkər çörəyi"
    assert mixed_cells[(1, 2)] == "Описание" and mixed_cells[(1, 3)] == "Price"
    assert mixed_cells[(2, 2)] == "Сахар белый"


def test_merged_cell_regions_match_the_fixture_truth(
    table_fixtures: dict[str, TableFixture],
) -> None:
    score = _score(table_fixtures["merged_cells.pdf"])
    table = _capture(table_fixtures["merged_cells.pdf"])["tables"][0]
    truth = table_fixtures["merged_cells.pdf"].tables[0]

    assert score["cell_f1"] == 1.0, score
    assert [
        (item["row"], item["col"], item["row_span"], item["col_span"]) for item in table["merged"]
    ] == list(truth.merged)
    # A merged region keeps one text at its anchor, so the covered position is not a second cell.
    assert len(table["cells"]) == len(truth.cells)


def test_empty_cells_are_kept_as_empty_text(table_fixtures: dict[str, TableFixture]) -> None:
    table = _capture(table_fixtures["empty_cells.pdf"])["tables"][0]

    cells = {(cell["row"], cell["col"]): cell["text"] for cell in table["cells"]}
    assert len(table["cells"]) == table["rows"] * table["columns"]
    assert cells[(2, 2)] == "" and cells[(3, 1)] == "" and cells[(3, 3)] == ""


# --- multiple tables, multi-page, false positives ------------------------------------------------


def test_two_tables_on_one_page_are_both_found(table_fixtures: dict[str, TableFixture]) -> None:
    structure = _capture(table_fixtures["multiple_tables.pdf"])
    score = _score(table_fixtures["multiple_tables.pdf"])

    assert [table["locator"] for table in structure["tables"]] == [
        {"page": 1, "table_index": 1},
        {"page": 1, "table_index": 2},
    ]
    assert score["matched_tables"] == 2
    assert score["false_positives"] == 0
    assert score["cell_f1"] == 1.0, score


def test_a_table_across_pages_is_reported_page_by_page(
    table_fixtures: dict[str, TableFixture],
) -> None:
    structure = _capture(table_fixtures["multi_page_table.pdf"])
    score = _score(table_fixtures["multi_page_table.pdf"])

    assert [
        (table["locator"]["page"], table["locator"]["table_index"]) for table in structure["tables"]
    ] == [
        (1, 1),
        (2, 1),
        (3, 1),
    ]
    assert score["detection_recall"] == 1.0
    assert score["false_positives"] == 0


def test_ordinary_two_column_text_is_not_detected_as_a_table(
    table_fixtures: dict[str, TableFixture],
) -> None:
    score = _score(table_fixtures["not_a_table.pdf"])

    assert score["predicted_tables"] == 0
    assert score["false_positives"] == 0


def test_a_scanned_table_is_an_honest_miss(
    table_fixtures: dict[str, TableFixture],
) -> None:
    """A rules-only page carries no native text, so M4 reports no table rather than guessing."""

    fixture = table_fixtures["scanned_table.pdf"]
    parsed = _parse(fixture)
    structure = _capture(fixture)

    assert structure["tables"] == []
    assert fixture.tables == ()
    assert parsed.requires_ocr is True
    assert any("no extractable text" in warning for warning in parsed.warnings)
    assert "layout" not in parsed.metadata


# --- content preservation and the aggregate report ------------------------------------------------


def test_the_flat_blocks_of_a_table_page_are_untouched(
    table_fixtures: dict[str, TableFixture],
) -> None:
    fixture = table_fixtures["azerbaijani_table.pdf"]
    parsed = _parse(fixture)
    structure = _capture(fixture)

    assert structure["blocks"] == parsed.as_dict()["blocks"]
    # The page's own ``page_text`` block still carries every cell text: the table is a view only.
    page_text = " ".join(block["text"] for block in structure["blocks"])
    for line in fixture.lines:
        assert line in page_text


def test_aggregate_metrics_over_every_table_fixture(
    table_fixtures: dict[str, TableFixture],
) -> None:
    truth_tables = predicted_tables = matched_tables = false_positives = 0
    truth_cells = predicted_cells = matched_cells = 0
    shape_correct = locators_preserved = 0
    for fixture in table_fixtures.values():
        score = _score(fixture)
        truth_tables += cast("int", score["truth_tables"])
        predicted_tables += cast("int", score["predicted_tables"])
        matched_tables += cast("int", score["matched_tables"])
        false_positives += cast("int", score["false_positives"])
        truth_cells += cast("int", score["truth_cells"])
        predicted_cells += cast("int", score["predicted_cells"])
        matched_cells += cast("int", score["matched_cells"])
        shape_correct += cast("int", score["shape_correct_tables"])
        locators_preserved += cast("int", score["locators_preserved"])

    assert truth_tables == predicted_tables == matched_tables == 11
    assert false_positives == 0
    assert truth_cells == predicted_cells == matched_cells == 104
    assert shape_correct == 11
    assert locators_preserved == 11


def test_every_declared_fixture_is_generated(
    table_fixtures: dict[str, TableFixture],
) -> None:
    assert set(table_fixtures) == set(_TABLE_NAMES)
