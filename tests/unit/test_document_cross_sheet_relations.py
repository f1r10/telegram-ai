"""Phase 2 M7: cross-sheet structure is represented with its cue — never answered.

Every fixture is a real workbook built with openpyxl, parsed by the flag-on ``XlsxParser``, and
turned into the structure view. The assertions are about edges, endpoints and cues, so a relation
that would have to be guessed is exactly what the negative tests fail on.
"""

from pathlib import Path
from typing import Any, cast

from openpyxl import Workbook
from openpyxl.workbook.defined_name import DefinedName

from office_assistant.processing import structure as structure_module
from office_assistant.processing.parsers import XlsxParser
from office_assistant.processing.structure import build_document_structure

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _workbook(
    path: Path,
    sheets: list[tuple[str, list[list[object]]]],
    names: dict[str, str] | None = None,
) -> Path:
    workbook = Workbook()
    default = cast(Any, workbook.active)
    for index, (title, rows) in enumerate(sheets):
        sheet = default if index == 0 else workbook.create_sheet()
        sheet.title = title
        for row in rows:
            sheet.append(list(row))
    for name, destination in (names or {}).items():
        workbook.defined_names[name] = DefinedName(name, attr_text=destination)
    workbook.save(str(path))
    workbook.close()
    return path


def _parse(path: Path, *, capture: bool = True) -> Any:
    return XlsxParser(capture_cached_values=capture).parse(path, source_file_id=21, media_type=XLSX)


def _structure(parsed: Any) -> dict[str, Any]:
    payload = parsed.as_dict()
    metadata = payload["metadata"]
    return build_document_structure(
        payload["blocks"], metadata.get("layout"), metadata.get("xlsx")
    ).as_dict()


def _populated_cells(parsed: Any) -> set[tuple[str, str]]:
    """Every corner address a row block's range names, so an endpoint can be checked to exist."""

    cells: set[tuple[str, str]] = set()
    for block in parsed.blocks:
        locator = block.locator
        if "sheet" not in locator:
            continue
        head, _, tail = str(locator["range"]).partition(":")
        cells.add((str(locator["sheet"]), head))
        cells.add((str(locator["sheet"]), tail or head))
    return cells


def _relations_of(parsed: Any, cue_type: str) -> list[dict[str, Any]]:
    return [
        relation
        for relation in _structure(parsed)["relations"]
        if relation["cue"]["type"] == cue_type
    ]


def test_two_sheets_sharing_a_key_column_are_related_by_that_column(tmp_path: Path) -> None:
    parsed = _parse(
        _workbook(
            tmp_path / "prices.xlsx",
            [
                ("Qiymətlər", [["SKU", "Qiymət"], ["KA45", 10], ["KA46", 12]]),
                ("Satışlar", [["SKU", "Miqdar"], ["KA45", 3], ["KA47", 1]]),
            ],
        )
    )

    structure = _structure(parsed)

    assert structure["relations"] == [
        {
            "id": "relation-1",
            "source": {"sheet": "Qiymətlər", "range": "A2:A3"},
            "target": {"sheet": "Satışlar", "range": "A2:A3"},
            "cue": {"type": "shared_key_column", "header": "SKU", "shared_values": 1},
        }
    ], "the shared header is the cue and the shared value is the evidence"


def test_both_ends_of_a_relation_name_cells_the_workbook_really_holds(tmp_path: Path) -> None:
    parsed = _parse(
        _workbook(
            tmp_path / "prices.xlsx",
            [
                ("Qiymətlər", [["SKU", "Qiymət"], ["KA45", 10], ["KA46", 12]]),
                ("Satışlar", [["SKU", "Miqdar"], ["KA45", 3]]),
            ],
        )
    )
    cells = _populated_cells(parsed)

    for relation in _structure(parsed)["relations"]:
        assert relation["source"]["sheet"] in {"Qiymətlər", "Satışlar"}
        assert relation["target"]["sheet"] in {"Qiymətlər", "Satışlar"}
        for endpoint in (relation["source"], relation["target"]):
            head, _, tail = str(endpoint["range"]).partition(":")
            assert (endpoint["sheet"], head) in cells
            assert (endpoint["sheet"], tail or head) in cells


def test_no_relation_is_invented_when_no_cue_exists(tmp_path: Path) -> None:
    """Same shape as the key-column case, but no shared header and no shared value."""

    parsed = _parse(
        _workbook(
            tmp_path / "unrelated.xlsx",
            [
                ("Qiymətlər", [["SKU", "Qiymət"], ["KA45", 10]]),
                ("Satışlar", [["Kod", "Miqdar"], ["ZZ9", 3]]),
            ],
        )
    )

    assert _structure(parsed)["relations"] == []


def test_a_formula_naming_another_sheet_is_an_edge_with_the_formula_as_its_cue(
    tmp_path: Path,
) -> None:
    parsed = _parse(
        _workbook(
            tmp_path / "total.xlsx",
            [
                ("Qiymətlər", [["SKU", "Qiymət"], ["KA45", 10], ["KA46", 12]]),
                (
                    "Satışlar",
                    [["Kod", "Cəm"], [None, None], [None, None], ["KA45", "=SUM(Qiymətlər!B2:B3)"]],
                ),
            ],
        )
    )

    formula_edges = _relations_of(parsed, "formula_reference")

    assert formula_edges == [
        {
            "id": formula_edges[0]["id"],
            "source": {"sheet": "Satışlar", "range": "B4"},
            "target": {"sheet": "Qiymətlər", "range": "B2:B3"},
            "cue": {
                "type": "formula_reference",
                "reference": "Qiymətlər!B2:B3",
            },
        }
    ]


def test_a_defined_name_a_formula_uses_points_at_its_own_destination(tmp_path: Path) -> None:
    parsed = _parse(
        _workbook(
            tmp_path / "named.xlsx",
            [
                ("Qiymətlər", [["SKU", "Qiymət"], ["KA45", 10], ["KA46", 12]]),
                ("Hesab", [["Nəticə"], [None], [None], ["=TOTAL*2"]]),
            ],
            names={"TOTAL": "'Qiymətlər'!$B$2:$B$3"},
        )
    )

    name_edges = _relations_of(parsed, "defined_name")

    assert name_edges == [
        {
            "id": name_edges[0]["id"],
            "source": {"sheet": "Hesab", "range": "A4"},
            "target": {"sheet": "Qiymətlər", "range": "B2:B3"},
            "cue": {
                "type": "defined_name",
                "name": "TOTAL",
                "reference": "Qiymətlər!B2:B3",
            },
        }
    ]


def test_a_reference_to_a_missing_sheet_or_an_unused_row_is_not_an_edge(tmp_path: Path) -> None:
    parsed = _parse(
        _workbook(
            tmp_path / "broken.xlsx",
            [
                ("Qiymətlər", [["SKU", "Qiymət"], ["KA45", 10]]),
                (
                    "Satışlar",
                    [["Kod", "Cəm"], ["X", "=Yoxdur!A1"], ["Y", "=Qiymətlər!A99"]],
                ),
            ],
        )
    )

    assert _relations_of(parsed, "formula_reference") == [], (
        "an endpoint that cannot be followed is not recorded"
    )


def test_a_single_sheet_workbook_gains_no_relations_and_no_other_change(tmp_path: Path) -> None:
    path = _workbook(
        tmp_path / "single.xlsx",
        [("Qiymətlər", [["SKU", "Qiymət"], ["KA45", 10], ["KA46", "=B2*2"]])],
        names={"TOTAL": "'Qiymətlər'!$B$2:$B$3"},
    )
    parsed = _parse(path)
    payload = parsed.as_dict()
    without_sidecar = build_document_structure(payload["blocks"]).as_dict()
    with_sidecar = _structure(parsed)

    assert with_sidecar["relations"] == []
    assert with_sidecar == without_sidecar, "the pre-M7 view, unchanged"


def test_the_view_stops_at_its_relation_bound(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(structure_module, "_MAX_CROSS_SHEET_RELATIONS", 2)
    parsed = _parse(
        _workbook(
            tmp_path / "many.xlsx",
            [
                ("A", [["SKU"], ["KA45"]]),
                ("B", [["SKU"], ["KA45"]]),
                ("C", [["SKU"], ["KA45"]]),
            ],
        )
    )

    relations = _structure(parsed)["relations"]

    assert len(relations) == 2
    assert [relation["id"] for relation in relations] == ["relation-1", "relation-2"]


def test_defined_names_are_recorded_only_when_the_flag_is_on(tmp_path: Path) -> None:
    path = _workbook(
        tmp_path / "named.xlsx",
        [("Qiymətlər", [["SKU", "Qiymət"], ["KA45", 10]])],
        names={"TOTAL": "'Qiymətlər'!$B$2:$B$2"},
    )

    assert "xlsx" not in _parse(path, capture=False).metadata
    assert _parse(path).metadata["xlsx"]["defined_names"] == [
        {"name": "TOTAL", "destination": "'Qiymətlər'!$B$2:$B$2"}
    ]
