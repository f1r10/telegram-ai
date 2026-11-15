"""Phase 2 M3: geometric reading order and page-furniture detection inside the structure view.

The reading order and the furniture list are recorded *inside* ``DocumentStructure`` and never
rewrite the flat ``blocks`` list: the raw content-stream text keeps its own (defective, gap G7)
order while ``page["lines"]`` recovers the column order, and a repeated header/footer/page number
stays in the artifact (gap G8) even though retrieval should not read it as body text. Everything
here is deterministic and offline — no PDF is opened.
"""

from __future__ import annotations

import json
from typing import Any

from office_assistant.processing.models import ContentBlock, ParsedDocument
from office_assistant.processing.structure import build_document_structure, furniture_key
from office_assistant.processing.worker_service import build_normalized_payload


def _line(text: str, x: float, y: float, size: float = 12.0) -> dict[str, Any]:
    return {"text": text, "x": x, "y": y, "size": size}


def _page(number: int, lines: list[dict[str, Any]]) -> dict[str, Any]:
    return {"page": number, "width": 612.0, "height": 792.0, "lines": lines}


def _layout(pages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"pages": pages}


def _page_block(sequence: int, page: int, text: str) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "block_type": "page_text",
        "text": text,
        "locator": {"page": page},
    }


def _texts(page: dict[str, Any]) -> list[str]:
    return [line["text"] for line in page["lines"]]


# --- reading order -------------------------------------------------------------------------------


def test_reading_order_recovers_two_columns_from_an_interleaved_stream() -> None:
    # The content stream interleaves the columns (LEFT-1, RIGHT-1, ...): the defect being measured.
    blocks = [_page_block(1, 1, "LEFT-1 RIGHT-1 LEFT-2 RIGHT-2")]
    layout = _layout(
        [
            _page(
                1,
                [
                    _line("LEFT-1", 72.0, 700.0),
                    _line("RIGHT-1", 320.0, 700.0),
                    _line("LEFT-2", 72.0, 684.0),
                    _line("RIGHT-2", 320.0, 684.0),
                ],
            )
        ]
    )

    structure = build_document_structure(blocks, layout)

    assert _texts(structure.pages[0]) == ["LEFT-1", "LEFT-2", "RIGHT-1", "RIGHT-2"]


def test_reading_order_keeps_a_full_width_title_above_the_columns() -> None:
    blocks = [_page_block(1, 1, "TITLE L-1 R-1 L-2 R-2")]
    layout = _layout(
        [
            _page(
                1,
                [
                    _line("TITLE", 72.0, 744.0),
                    _line("L-1", 72.0, 700.0),
                    _line("R-1", 320.0, 700.0),
                    _line("L-2", 72.0, 684.0),
                    _line("R-2", 320.0, 684.0),
                ],
            )
        ]
    )

    structure = build_document_structure(blocks, layout)

    assert _texts(structure.pages[0]) == ["TITLE", "L-1", "L-2", "R-1", "R-2"]


def test_page_view_without_layout_keeps_the_parser_order() -> None:
    structure = build_document_structure([_page_block(1, 1, "anything")])

    assert structure.pages == [{"page": 1, "reading_order": [1], "furniture": []}]
    assert "lines" not in structure.pages[0]


# --- page furniture ------------------------------------------------------------------------------


def test_page_number_digits_are_masked_into_one_furniture_key() -> None:
    assert furniture_key("Page 1") == furniture_key("Page 2") == "page #"
    assert furniture_key("  INTERNAL   Document ") == "internal document"


def test_repeated_header_and_page_number_are_detected_as_furniture() -> None:
    blocks: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    for number in range(1, 4):
        blocks.append(_page_block(number, number, f"Internal Document BODY-{number} Page {number}"))
        pages.append(
            _page(
                number,
                [
                    _line("Internal Document", 72.0, 760.0),
                    _line(f"BODY-{number}", 72.0, 600.0),
                    _line(f"Page {number}", 72.0, 40.0),
                ],
            )
        )

    structure = build_document_structure(blocks, _layout(pages))

    for number in range(1, 4):
        page = structure.pages[number - 1]
        assert _texts(page) == [f"BODY-{number}"]
        assert {item["key"] for item in page["furniture"]} == {"internal document", "page #"}
        assert page["furniture"][0]["locator"] == {"page": number}
        assert page["furniture"][0]["repeated_on"] == [1, 2, 3]


def test_inverted_guard_no_furniture_when_nothing_repeats_across_pages() -> None:
    # A repeated body clause plus per-page-unique (word-varying, not digit-varying) header/footer:
    # nothing shares a furniture key, so nothing is furniture and every line stays in the order.
    repeated = "THIS CLAUSE APPLIES TO ALL AGREEMENTS"
    headers = ("ALPHA INTERNAL NOTICE", "BETA INTERNAL NOTICE", "GAMMA INTERNAL NOTICE")
    footers = ("REVISION ALPHA", "REVISION BETA", "REVISION GAMMA")
    blocks: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    for index in range(3):
        blocks.append(_page_block(index + 1, index + 1, f"{headers[index]} {repeated}"))
        pages.append(
            _page(
                index + 1,
                [
                    _line(headers[index], 72.0, 760.0),
                    _line(f"BODY-{index + 1}", 72.0, 500.0),
                    _line(repeated, 72.0, 400.0),
                    _line(footers[index], 72.0, 40.0),
                ],
            )
        )

    structure = build_document_structure(blocks, _layout(pages))

    for number in range(1, 4):
        page = structure.pages[number - 1]
        assert page["furniture"] == []
        assert repeated in _texts(page)


def test_one_page_document_never_reports_furniture() -> None:
    blocks = [_page_block(1, 1, "Header body Footer")]
    layout = _layout([_page(1, [_line("Header", 72.0, 760.0), _line("Footer", 72.0, 40.0)])])

    structure = build_document_structure(blocks, layout)

    assert structure.pages[0]["furniture"] == []


# --- content preservation and the flat blocks ----------------------------------------------------


def test_furniture_never_removes_the_line_from_the_artifact() -> None:
    blocks: list[dict[str, Any]] = [_page_block(1, 1, "Internal Document BODY-1 Page 1")]
    layout = _layout(
        [
            _page(
                1,
                [
                    _line("Internal Document", 72.0, 760.0),
                    _line("BODY-1", 72.0, 600.0),
                    _line("Page 1", 72.0, 40.0),
                ],
            )
        ]
    )

    structure = build_document_structure(blocks, layout)

    # With only one page nothing repeats, but the invariant must hold on the artifact regardless:
    # the structure view is a view, so ``blocks`` is byte-identical to the parser's own output.
    assert structure.blocks == blocks
    assert structure.pages[0]["reading_order"] == [1]


def test_flag_on_adds_reading_order_without_touching_the_flat_blocks() -> None:
    blocks = [ContentBlock(1, "page_text", "LEFT-1 RIGHT-1 LEFT-2 RIGHT-2", {"page": 1})]
    layout = _layout(
        [
            _page(
                1,
                [
                    _line("LEFT-1", 72.0, 700.0),
                    _line("RIGHT-1", 320.0, 700.0),
                    _line("LEFT-2", 72.0, 684.0),
                    _line("RIGHT-2", 320.0, 684.0),
                ],
            )
        ]
    )
    parsed = ParsedDocument(
        source_file_id=1,
        parser_name="pypdf",
        parser_version="7.0.0",
        media_type="application/pdf",
        metadata={"layout": layout},
        blocks=blocks,
    )

    payload = build_normalized_payload(parsed, [], document_intelligence_enabled=True)

    assert payload["blocks"] == parsed.as_dict()["blocks"]
    assert _texts(payload["structure"]["pages"][0]) == ["LEFT-1", "LEFT-2", "RIGHT-1", "RIGHT-2"]


def test_flag_off_ignores_the_layout_sidecar_entirely() -> None:
    parsed = ParsedDocument(
        source_file_id=1,
        parser_name="pypdf",
        parser_version="7.0.0",
        media_type="application/pdf",
        metadata={"layout": _layout([_page(1, [_line("A", 72.0, 700.0)])])},
        blocks=[ContentBlock(1, "page_text", "A", {"page": 1})],
    )

    payload = build_normalized_payload(parsed, [], document_intelligence_enabled=False)

    assert "structure" not in payload
    expected = parsed.as_dict()
    expected["session_items"] = []
    assert json.dumps(payload, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)


def test_reading_order_view_is_deterministic() -> None:
    blocks = [_page_block(1, 1, "LEFT-1 RIGHT-1 LEFT-2 RIGHT-2")]
    layout = _layout(
        [
            _page(
                1,
                [
                    _line("LEFT-1", 72.0, 700.0),
                    _line("RIGHT-1", 320.0, 700.0),
                    _line("LEFT-2", 72.0, 684.0),
                    _line("RIGHT-2", 320.0, 684.0),
                ],
            )
        ]
    )

    first = build_document_structure(blocks, layout).as_dict()
    second = build_document_structure(blocks, layout).as_dict()

    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
