"""Phase 2 M2: the document-structure view is additive, deterministic and flag-gated.

These tests pin the compatibility contract of the structure layer: with the flag off the derived
artifact is byte-for-byte the parser's own output, the flat ``blocks`` list is never altered, and
chunking produces identical ``chunk_id``s whether or not a ``structure`` key is present.
"""

import json
from typing import Any

from office_assistant.core.settings import Settings
from office_assistant.global_intelligence.flags import EngineFlags, engine_flags
from office_assistant.knowledge.chunking import chunk_normalized_document
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.processing.models import ContentBlock, ParsedDocument
from office_assistant.processing.structure import build_document_structure
from office_assistant.processing.worker_service import build_normalized_payload


def _raw(
    sequence: int,
    block_type: str,
    text: str,
    locator: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "sequence": sequence,
        "block_type": block_type,
        "text": text,
        "locator": locator,
    }
    if metadata is not None:
        block["metadata"] = metadata
    return block


def _docx_parsed() -> ParsedDocument:
    return ParsedDocument(
        source_file_id=1,
        parser_name="python-docx",
        parser_version="1.2.0",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        metadata={},
        blocks=[
            ContentBlock(1, "heading", "1 Giriş", {"paragraph_index": 0}),
            ContentBlock(2, "paragraph", "Mətn.", {"paragraph_index": 1}),
            ContentBlock(
                3, "table_row", "Ad | Say", {"table_index": 0, "row": 1}, {"cells": ["Ad", "Say"]}
            ),
        ],
    )


# --- flag off: byte-for-byte compatibility ---------------------------------------------------


def test_flag_off_adds_no_structure_and_keeps_the_artifact_identical() -> None:
    parsed = ParsedDocument(
        source_file_id=1,
        parser_name="pypdf",
        parser_version="1.0",
        media_type="application/pdf",
        metadata={"ocr": {"engine": "tesseract"}},
        blocks=[ContentBlock(1, "page_text", "Salam", {"page": 1})],
    )

    payload = build_normalized_payload(parsed, [], document_intelligence_enabled=False)

    assert "structure" not in payload
    expected = parsed.as_dict()
    expected["session_items"] = []
    assert json.dumps(payload, ensure_ascii=False, indent=2) == json.dumps(
        expected, ensure_ascii=False, indent=2
    )


def test_inverted_guard_flag_off_payload_chunks_exactly_as_the_bare_artifact() -> None:
    parsed = _docx_parsed()
    config = ChunkingConfig("v1-test", 1000, 50)

    payload = build_normalized_payload(parsed, [], document_intelligence_enabled=False)
    assert "structure" not in payload
    from_payload = chunk_normalized_document(
        payload, source_file_id=1, parse_run_id=2, config=config
    )
    from_artifact = chunk_normalized_document(
        parsed.as_dict(), source_file_id=1, parse_run_id=2, config=config
    )

    assert [chunk.chunk_id for chunk in from_payload] == [chunk.chunk_id for chunk in from_artifact]


# --- flag on: additive structure -------------------------------------------------------------


def test_flag_on_adds_structure_without_touching_the_flat_blocks() -> None:
    parsed = _docx_parsed()

    payload = build_normalized_payload(
        parsed, [{"sequence": 1}], document_intelligence_enabled=True
    )

    assert payload["blocks"] == parsed.as_dict()["blocks"]
    structure = payload["structure"]
    assert set(structure) == {"pages", "sections", "tables", "lists", "relations", "blocks"}
    assert structure["blocks"] == payload["blocks"]
    assert payload["session_items"] == [{"sequence": 1}]


def test_structure_is_deterministic() -> None:
    blocks = _docx_parsed().as_dict()["blocks"]

    first = build_document_structure(blocks).as_dict()
    second = build_document_structure(blocks).as_dict()

    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )


def test_pages_group_page_blocks_in_reading_order() -> None:
    structure = build_document_structure(
        [
            _raw(1, "page_text", "A", {"page": 1}),
            _raw(2, "page_ocr", "B", {"page": 1, "extraction": "tesseract"}),
            _raw(3, "page_text", "C", {"page": 2}),
            _raw(4, "plain_text", "D", {"line_start": 1, "line_end": 2}),
        ]
    )

    assert structure.pages == [
        {"page": 1, "reading_order": [1, 2], "furniture": []},
        {"page": 2, "reading_order": [3], "furniture": []},
    ]


def test_sections_follow_the_heading_hierarchy() -> None:
    structure = build_document_structure(
        [
            _raw(1, "heading", "1 Giriş", {"paragraph_index": 0}),
            _raw(2, "paragraph", "mətn", {"paragraph_index": 1}),
            _raw(3, "heading", "1.1 Detallar", {"paragraph_index": 2}),
            _raw(4, "paragraph", "detallı mətn", {"paragraph_index": 3}),
            _raw(5, "heading", "2 Yekun", {"paragraph_index": 4}),
            _raw(6, "paragraph", "son", {"paragraph_index": 5}),
        ]
    )

    assert [section["title"] for section in structure.sections] == [
        "1 Giriş",
        "1.1 Detallar",
        "2 Yekun",
    ]
    assert [section["level"] for section in structure.sections] == [1, 2, 1]
    assert structure.sections[0]["block_range"] == [1, 4]
    assert structure.sections[1]["block_range"] == [3, 4]
    assert structure.sections[2]["block_range"] == [5, 6]
    assert structure.sections[0]["locator"] == {"paragraph_index": 0}


def test_unnumbered_heading_is_recorded_at_level_one() -> None:
    structure = build_document_structure([_raw(1, "heading", "Ümumi", {"paragraph_index": 0})])

    assert structure.sections[0]["level"] == 1


def test_docx_table_rows_group_into_one_table_with_cell_coordinates() -> None:
    structure = build_document_structure(
        [
            _raw(
                1, "table_row", "Ad | Say", {"table_index": 0, "row": 1}, {"cells": ["Ad", "Say"]}
            ),
            _raw(
                2, "table_row", "Kabel | 3", {"table_index": 0, "row": 2}, {"cells": ["Kabel", "3"]}
            ),
        ]
    )

    assert len(structure.tables) == 1
    table = structure.tables[0]
    assert table["id"] == "docx-table-0"
    assert table["source"] == {"format": "docx", "table_index": 0}
    assert table["locator"] == {"table_index": 0}
    assert table["rows"] == 2
    assert table["columns"] == 2
    assert table["cells"][0] == {"row": 1, "col": 1, "text": "Ad"}
    assert table["cells"][3] == {"row": 2, "col": 2, "text": "3"}


def test_xlsx_rows_group_by_sheet_with_rows_taken_from_the_range() -> None:
    structure = build_document_structure(
        [
            _raw(
                1,
                "table_row",
                "Ad | Say",
                {"sheet": "Anbar", "range": "A1:B1"},
                {"values": ["Ad", "Say"], "columns": ["A", "B"]},
            ),
            _raw(
                2,
                "table_row",
                "Kabel | 3",
                {"sheet": "Anbar", "range": "A5:B5"},
                {"values": ["Kabel", 3], "columns": ["A", "B"]},
            ),
            _raw(
                3,
                "table_row",
                "X | Y",
                {"sheet": "Satış", "range": "A1:B1"},
                {"values": ["X", "Y"], "columns": ["A", "B"]},
            ),
        ]
    )

    assert [table["id"] for table in structure.tables] == [
        "xlsx-sheet-Anbar",
        "xlsx-sheet-Satış",
    ]
    anbar = structure.tables[0]
    assert anbar["source"] == {"format": "xlsx", "sheet": "Anbar"}
    assert anbar["locator"] == {"sheet": "Anbar"}
    assert anbar["rows"] == 2
    assert anbar["cells"][0] == {"row": 1, "col": 1, "text": "Ad"}
    assert anbar["cells"][2] == {"row": 5, "col": 1, "text": "Kabel"}


def test_lists_and_relations_are_reserved_and_empty() -> None:
    structure = build_document_structure([_raw(1, "paragraph", "x", {"paragraph_index": 0})])

    assert structure.lists == []
    assert structure.relations == []


def test_original_block_locators_are_preserved_verbatim() -> None:
    blocks = [
        _raw(1, "heading", "Başlıq", {"paragraph_index": 0}),
        _raw(2, "table_row", "A | B", {"sheet": "S", "range": "A1:B1"}, {"values": ["A", "B"]}),
    ]

    structure = build_document_structure(blocks)

    assert structure.blocks == blocks
    assert structure.sections[0]["locator"] == {"paragraph_index": 0}
    assert structure.tables[0]["locator"] == {"sheet": "S"}


# --- chunking is unaffected by the presence of structure --------------------------------------


def _chunking_document() -> dict[str, Any]:
    return {
        "blocks": [
            {"sequence": 1, "block_type": "heading", "text": "Zəmanət", "locator": {"page": 2}},
            {
                "sequence": 2,
                "block_type": "paragraph",
                "text": "KA45 məhsulunun zəmanət müddəti 24 aydır.",
                "locator": {"page": 2},
            },
            {
                "sequence": 3,
                "block_type": "table_row",
                "text": "KA46 | 12 | AZN",
                "locator": {"sheet": "Products", "range": "A42:C42"},
            },
        ]
    }


def test_chunking_output_is_unchanged_by_the_presence_of_structure() -> None:
    document = _chunking_document()
    with_structure = dict(document)
    with_structure["structure"] = build_document_structure(document["blocks"]).as_dict()
    config = ChunkingConfig("v1-medium-1800-180", 1800, 180)

    plain = chunk_normalized_document(document, source_file_id=10, parse_run_id=20, config=config)
    structured = chunk_normalized_document(
        with_structure, source_file_id=10, parse_run_id=20, config=config
    )

    assert [chunk.chunk_id for chunk in plain] == [chunk.chunk_id for chunk in structured]
    assert [chunk.text for chunk in plain] == [chunk.text for chunk in structured]
    assert [chunk.locator for chunk in plain] == [chunk.locator for chunk in structured]


# --- the flag is off by default ---------------------------------------------------------------


def test_document_intelligence_flag_defaults_off() -> None:
    assert EngineFlags().document_intelligence_enabled is False
    assert Settings().gi_document_intelligence_enabled is False
    assert engine_flags(Settings()).document_intelligence_enabled is False


def test_document_intelligence_flag_follows_settings_when_opted_in() -> None:
    flags = engine_flags(Settings(gi_document_intelligence_enabled=True))

    assert flags.document_intelligence_enabled is True
