"""Phase 2 M5: spreadsheet cached values and merged regions are additive and flag-gated.

The milestone exists because a formula cell reported only its formula: the computed result the
workbook stored beside it was never recorded, so a later reader could not see the number the
spreadsheet displays. These tests pin the contract that (a) the flag-off artifact is exactly the
pre-M5 row metadata, (b) a cached result is recorded *beside* its formula, never replacing it,
(c) a formula with no cached result warns instead of inventing a value, and (d) merged regions are
recorded with a locator that resolves to the anchor cell.
"""

from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.worksheet.table import Table

from evaluation.fixtures import xlsx_with_cached_values
from office_assistant.processing.parsers import XlsxParser
from office_assistant.processing.structure import build_document_structure

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Two sheets sharing a product code, with a formula on the second sheet — the M5 shape.
_SHEETS: tuple[tuple[str, tuple[tuple[object, ...], ...]], ...] = (
    (
        "Qaliq",
        (
            ("Kod", "Ad", "Miqdar"),
            ("PRN-M404", "HP LaserJet Pro M404dn", 4),
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


def _workbook(tmp_path: Path, cached: dict[int, dict[str, object]] | None = None) -> Path:
    path = tmp_path / "workbook.xlsx"
    path.write_bytes(xlsx_with_cached_values(_SHEETS, cached))
    return path


def _parse(path: Path, *, capture_cached_values: bool) -> Any:
    return XlsxParser(capture_cached_values=capture_cached_values).parse(
        path, source_file_id=1, media_type=_XLSX_MEDIA_TYPE
    )


def _formula_block(parsed: Any) -> Any:
    for block in parsed.blocks:
        if "=SUM(B2:B3)" in block.text:
            return block
    raise AssertionError("the formula row was not parsed")


def test_flag_off_keeps_the_pre_m5_row_metadata(tmp_path: Path) -> None:
    parsed = _parse(_workbook(tmp_path), capture_cached_values=False)

    assert parsed.metadata == {}
    for block in parsed.blocks:
        assert set(block.metadata) == {"values", "formulas", "columns"}


def test_a_cached_result_is_recorded_beside_the_formula(tmp_path: Path) -> None:
    parsed = _parse(_workbook(tmp_path, {1: {"B4": 7}}), capture_cached_values=True)
    block = _formula_block(parsed)

    values: list[Any] = block.metadata["values"]
    index = values.index("=SUM(B2:B3)")
    assert block.metadata["formulas"][index] is True
    # Both sides are kept: the formula string is not replaced by its result.
    assert block.metadata["cached_values"][index] == 7
    assert parsed.warnings == []


def test_a_missing_cached_result_warns_instead_of_inventing_zero(tmp_path: Path) -> None:
    parsed = _parse(_workbook(tmp_path), capture_cached_values=True)
    block = _formula_block(parsed)

    index: int = block.metadata["values"].index("=SUM(B2:B3)")
    assert block.metadata["cached_values"][index] is None
    assert any("cell B4" in warning and "cached" in warning for warning in parsed.warnings)
    # The row text still shows the formula, and no zero was fabricated.
    assert "=SUM(B2:B3)" in block.text


def test_row_text_is_identical_with_and_without_cached_values(tmp_path: Path) -> None:
    off = _parse(_workbook(tmp_path), capture_cached_values=False)
    on = _parse(_workbook(tmp_path, {1: {"B4": 7}}), capture_cached_values=True)

    assert off.text() == on.text()
    assert [block.sequence for block in off.blocks] == [block.sequence for block in on.blocks]


def test_merged_regions_and_declared_tables_are_recorded_per_sheet(tmp_path: Path) -> None:
    path = tmp_path / "structured.xlsx"
    workbook = Workbook()
    sheet = workbook.worksheets[0]
    sheet.title = "Qaliq"
    sheet.append(["Kod", "Ad", "Miqdar"])
    sheet.append(["PRN-M404", "HP LaserJet Pro M404dn", 4])
    sheet.merge_cells("A1:C1")
    # A declared table needs real string headers, so it lives on its own sheet: a merged header
    # inside a table region is not a valid workbook.
    stock = workbook.create_sheet("Anbar")
    stock.append(["Kod", "Ad", "Miqdar"])
    stock.append(["PRN-M404", "HP LaserJet Pro M404dn", 4])
    stock.append(["CAM-2143", "Hikvision DS-2CD2143G2-I", 12])
    stock.add_table(Table(displayName="Stock", ref="A1:C3"))
    workbook.save(path)

    parsed = _parse(path, capture_cached_values=True)
    sheets = parsed.metadata["xlsx"]["sheets"]

    assert sheets[0]["sheet"] == "Qaliq"
    assert sheets[0]["merged_regions"] == [{"range": "A1:C1", "anchor": "A1"}]
    assert "declared_tables" not in sheets[0]
    assert sheets[1]["sheet"] == "Anbar"
    assert sheets[1]["declared_tables"] == [{"name": "Stock", "range": "A1:C3"}]


def test_a_plain_sheet_gains_no_structural_keys(tmp_path: Path) -> None:
    parsed = _parse(_workbook(tmp_path), capture_cached_values=True)
    qaliq = parsed.metadata["xlsx"]["sheets"][0]

    # Inverted guard: no merge and no declared table means no keys, not empty placeholders.
    assert qaliq == {"sheet": "Qaliq"}
    assert "merged_regions" not in qaliq
    assert "declared_tables" not in qaliq


def test_the_structure_table_carries_merged_regions(tmp_path: Path) -> None:
    path = tmp_path / "merged.xlsx"
    workbook = Workbook()
    sheet = workbook.worksheets[0]
    sheet.title = "Qaliq"
    sheet.append(["Kod", "Ad", "Miqdar"])
    sheet.merge_cells("A1:C1")
    sheet.append(["PRN-M404", "HP LaserJet Pro M404dn", 4])
    workbook.save(path)
    parsed = _parse(path, capture_cached_values=True)

    blocks = [asdict(block) for block in parsed.blocks]
    structure = build_document_structure(blocks, None, parsed.metadata["xlsx"])
    table = structure.tables[0]

    assert table["id"] == "xlsx-sheet-Qaliq"
    assert table["merged_regions"] == [{"range": "A1:C1", "anchor": "A1"}]
    # The row cells are unchanged by the sidecar.
    assert {"row": 2, "col": 1, "text": "PRN-M404"} in table["cells"]


def test_structure_is_deterministic_with_sheet_metadata(tmp_path: Path) -> None:
    parsed = _parse(_workbook(tmp_path, {1: {"B4": 7}}), capture_cached_values=True)
    blocks = [asdict(block) for block in parsed.blocks]

    first = build_document_structure(blocks, None, parsed.metadata["xlsx"]).as_dict()
    second = build_document_structure(blocks, None, parsed.metadata["xlsx"]).as_dict()

    assert first == second
    assert first["tables"][0]["id"] == "xlsx-sheet-Qaliq"


def test_a_merged_workbook_reopens_with_the_same_cached_value(tmp_path: Path) -> None:
    """The generated fixture is a real XLSX, not a synthesised object graph."""

    from openpyxl import load_workbook

    path = _workbook(tmp_path, {1: {"B4": 7}})
    cached = load_workbook(BytesIO(path.read_bytes()), data_only=True)["Satis"]
    formulas = load_workbook(BytesIO(path.read_bytes()), data_only=False)["Satis"]

    assert cached["B4"].value == 7
    assert formulas["B4"].value == "=SUM(B2:B3)"
