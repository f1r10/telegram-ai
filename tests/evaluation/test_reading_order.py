"""M3: measured reading order and page-furniture detection on generated layout fixtures.

Every number here comes from a real run: the generated PDF is parsed by the shipped ``PdfParser``
with geometry capture, the structure layer computes the reading order and the furniture, and the M0
scorers compare them with the fixture truth. TEST_STRATEGY.md §5 makes these report-only (no
accuracy threshold); the hard gates — content preservation and the geometry being invisible to the
flat blocks — are asserted here as well. No fixture binary is committed (risk R7).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from office_assistant.processing.parsers import PdfParser
from office_assistant.processing.structure import build_document_structure

from .fixtures import LayoutFixture, generate_layout_fixtures
from .metrics import (
    score_content_preservation,
    score_furniture_detection,
    score_reading_order,
)

_LAYOUT_NAMES = (
    "two_column_contract.pdf",
    "three_column_contract.pdf",
    "mixed_layout.pdf",
    "different_page_layouts.pdf",
    "furniture.pdf",
    "legitimate_repetition.pdf",
)


@pytest.fixture(scope="module")
def layout_fixtures(tmp_path_factory: pytest.TempPathFactory) -> dict[str, LayoutFixture]:
    return generate_layout_fixtures(tmp_path_factory.mktemp("m3-layout"))


def _capture(fixture: LayoutFixture) -> dict[str, Any]:
    """Parse a fixture with geometry capture and return the derived structure view."""

    parsed = PdfParser(capture_layout=True).parse(
        fixture.path, source_file_id=0, media_type="application/pdf"
    )
    layout = cast("dict[str, Any] | None", parsed.metadata.get("layout"))
    return build_document_structure(parsed.as_dict()["blocks"], layout).as_dict()


def _body_texts(pages: list[dict[str, Any]], number: int) -> list[str]:
    page = next(item for item in pages if item["page"] == number)
    return [line["text"] for line in page["lines"]]


# --- reading order -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", _LAYOUT_NAMES)
def test_reading_order_exact_sequence_match_on_every_page(
    name: str, layout_fixtures: dict[str, LayoutFixture]
) -> None:
    fixture = layout_fixtures[name]
    pages: list[dict[str, Any]] = _capture(fixture)["pages"]

    assert len(pages) == len(fixture.page_order)
    for number, truth in enumerate(fixture.page_order, start=1):
        score = score_reading_order(truth, _body_texts(pages, number))
        assert score["exact_sequence_match"] is True, (name, number, score)
        assert score["pair_order_agreement"] == 1.0, (name, number, score)


def test_naive_content_stream_order_fails_the_two_column_truth(
    layout_fixtures: dict[str, LayoutFixture],
) -> None:
    """The measurement is meaningful: the interleaved stream order does *not* match the truth."""

    fixture = layout_fixtures["two_column_contract.pdf"]
    naive = score_reading_order(fixture.page_order[0], fixture.lines)

    assert naive["exact_sequence_match"] is False
    assert naive["pair_order_agreement"] is not None
    assert naive["pair_order_agreement"] < 1.0


# --- page furniture ------------------------------------------------------------------------------


def _detected_keys(pages: list[dict[str, Any]]) -> set[str]:
    return {item["key"] for page in pages for item in page["furniture"]}


def test_repeated_header_and_page_number_are_found_precisely(
    layout_fixtures: dict[str, LayoutFixture],
) -> None:
    fixture = layout_fixtures["furniture.pdf"]
    detected = _detected_keys(_capture(fixture)["pages"])
    score = score_furniture_detection(fixture.furniture, detected)

    assert score["precision"] == 1.0
    assert score["recall"] == 1.0
    assert score["false_positive_keys"] == 0


def test_inverted_guard_no_furniture_on_legitimate_repetition(
    layout_fixtures: dict[str, LayoutFixture],
) -> None:
    """A repeated *body* clause and per-page-unique furniture must flag nothing."""

    fixture = layout_fixtures["legitimate_repetition.pdf"]
    detected = _detected_keys(_capture(fixture)["pages"])
    score = score_furniture_detection(fixture.furniture, detected)

    assert detected == set()
    assert score["predicted_keys"] == 0
    assert score["false_positive_keys"] == 0
    assert score["precision"] is None and score["recall"] is None


# --- content preservation ------------------------------------------------------------------------


@pytest.mark.parametrize("name", _LAYOUT_NAMES)
def test_no_extracted_line_is_silently_discarded(
    name: str, layout_fixtures: dict[str, LayoutFixture]
) -> None:
    fixture = layout_fixtures[name]
    parsed = PdfParser(capture_layout=True).parse(
        fixture.path, source_file_id=0, media_type="application/pdf"
    )

    score = score_content_preservation(fixture.lines, parsed.text())

    assert score["preservation"] == 1.0, (name, score)


# --- the geometry is invisible to the flat artifact ----------------------------------------------


def test_geometry_capture_is_off_by_default_and_changes_nothing(
    layout_fixtures: dict[str, LayoutFixture],
) -> None:
    path: Path = layout_fixtures["two_column_contract.pdf"].path
    plain = PdfParser().parse(path, source_file_id=0, media_type="application/pdf")
    captured = PdfParser(capture_layout=True).parse(
        path, source_file_id=0, media_type="application/pdf"
    )

    assert "layout" not in plain.metadata
    assert "layout" in captured.metadata
    assert plain.as_dict()["blocks"] == captured.as_dict()["blocks"]
    assert plain.as_dict()["warnings"] == captured.as_dict()["warnings"]
    assert plain.as_dict()["requires_ocr"] == captured.as_dict()["requires_ocr"]
