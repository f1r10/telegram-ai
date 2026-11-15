# Third-party document libraries do not all ship complete Pyright stubs; this test builds raw
# WordprocessingML to exercise a paragraph-level outline level, which python-docx has no API for.
# pyright: reportPrivateUsage=false, reportUnknownVariableType=false, reportUnknownMemberType=false
"""Phase 2 M6: DOCX outline depth, list items, table coordinates and core properties.

python-docx already exposes the hierarchy, the numbering and the document properties; this
milestone records them instead of flattening a heading into an untyped paragraph and a list into
ordinary body text. Everything is additive and gated by ``gi_document_intelligence_enabled``:
with the flag off the blocks are exactly the pre-M6 blocks and the document metadata is empty.
"""

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from evaluation.fixtures import docx_bytes
from office_assistant.knowledge.chunking import chunk_normalized_document
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.processing.parsers import DocxParser
from office_assistant.processing.structure import build_document_structure

_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _parse(path: Path, *, capture_structure: bool) -> Any:
    return DocxParser(capture_structure=capture_structure).parse(
        path, source_file_id=1, media_type=_DOCX_MEDIA_TYPE
    )


def _blocks_as_dicts(parsed: Any) -> list[dict[str, Any]]:
    return [asdict(block) for block in parsed.blocks]


def test_heading_levels_follow_the_word_style(tmp_path: Path) -> None:
    path = tmp_path / "headings.docx"
    path.write_bytes(docx_bytes(((1, "Zəmanət"), (2, "Müddət"), (3, "İstisnalar"))))

    parsed = _parse(path, capture_structure=True)

    assert [block.block_type for block in parsed.blocks] == ["heading", "heading", "heading"]
    assert [block.metadata["level"] for block in parsed.blocks] == [1, 2, 3]
    assert {block.metadata["level_source"] for block in parsed.blocks} == {"style"}


def test_outline_level_wins_over_the_style_depth(tmp_path: Path) -> None:
    path = tmp_path / "outline.docx"
    document = Document()
    paragraph = document.add_heading("Dərin başlıq", level=1)
    properties = paragraph._p.get_or_add_pPr()
    outline = OxmlElement("w:outlineLvl")
    outline.set(qn("w:val"), "2")
    properties.append(outline)
    document.save(str(path))

    parsed = _parse(path, capture_structure=True)

    # A style depth of 1 would say level 1; the paragraph's own outlineLvl says level 3 (0-based).
    assert parsed.blocks[0].metadata == {"level": 3, "level_source": "outline"}


def test_numbered_and_bulleted_lists_become_list_items(tmp_path: Path) -> None:
    path = tmp_path / "lists.docx"
    document = Document()
    document.add_heading("Zəmanət", level=1)
    document.add_paragraph("24 ay", style="List Number")
    document.add_paragraph("Rəsmi servis", style="List Number")
    document.add_paragraph("Fiziki zədə", style="List Bullet")
    document.save(str(path))

    parsed = _parse(path, capture_structure=True)
    list_items = [block for block in parsed.blocks if block.block_type == "list_item"]

    assert [block.text for block in list_items] == ["24 ay", "Rəsmi servis", "Fiziki zədə"]
    assert [block.metadata["marker"] for block in list_items] == [
        "numbered",
        "numbered",
        "bulleted",
    ]
    assert [block.metadata["level"] for block in list_items] == [1, 1, 1]
    # The locator is the paragraph's own index and is not rewritten by the type change.
    assert [block.locator["paragraph_index"] for block in list_items] == [1, 2, 3]


def test_structure_groups_consecutive_list_items(tmp_path: Path) -> None:
    path = tmp_path / "lists.docx"
    document = Document()
    document.add_paragraph("Bir", style="List Number")
    document.add_paragraph("İki", style="List Number")
    document.add_paragraph("Ara başlıq")
    document.add_paragraph("Nöqtə", style="List Bullet")
    document.save(str(path))
    parsed = _parse(path, capture_structure=True)

    structure = build_document_structure(_blocks_as_dicts(parsed))

    assert [entry["id"] for entry in structure.lists] == ["list-1", "list-2"]
    assert [entry["marker"] for entry in structure.lists] == ["numbered", "bulleted"]
    assert [item["text"] for item in structure.lists[0]["items"]] == ["Bir", "İki"]
    assert structure.lists[1]["items"][0]["text"] == "Nöqtə"


def test_table_cells_record_grid_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "table.docx"
    document = Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Kod"
    table.cell(0, 1).text = "Qiymət"
    table.cell(1, 0).text = "KA45"
    table.cell(1, 1).text = "12"
    document.save(str(path))

    parsed = _parse(path, capture_structure=True)

    assert [block.metadata["cells"] for block in parsed.blocks] == [
        ["Kod", "Qiymət"],
        ["KA45", "12"],
    ]
    assert parsed.blocks[0].metadata["cell_coordinates"] == [
        {"row": 1, "column": 1},
        {"row": 1, "column": 2},
    ]
    assert parsed.blocks[1].metadata["cell_coordinates"] == [
        {"row": 2, "column": 1},
        {"row": 2, "column": 2},
    ]


def test_core_properties_are_recorded(tmp_path: Path) -> None:
    path = tmp_path / "props.docx"
    document = Document()
    document.add_paragraph("Mətn")
    document.core_properties.title = "Zəmanət siyasəti"
    document.core_properties.author = "Ofis"
    document.core_properties.subject = "Zəmanət"
    document.save(str(path))

    parsed = _parse(path, capture_structure=True)
    properties = parsed.metadata["core_properties"]

    assert properties["title"] == "Zəmanət siyasəti"
    assert properties["author"] == "Ofis"
    assert properties["subject"] == "Zəmanət"
    # Datetimes are JSON-serialisable strings, not raw objects.
    assert isinstance(properties["created"], str)
    assert datetime.fromisoformat(properties["created"]).tzinfo is not None


def test_flag_off_keeps_the_pre_m6_blocks_and_metadata(tmp_path: Path) -> None:
    path = tmp_path / "plain.docx"
    document = Document()
    document.add_heading("Başlıq", level=1)
    document.add_paragraph("Adi mətn")
    document.add_paragraph("Siyahı kimi görünür", style="List Number")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(0, 1).text = "B"
    document.save(str(path))

    parsed = _parse(path, capture_structure=False)

    assert parsed.metadata == {}
    assert [block.block_type for block in parsed.blocks] == [
        "heading",
        "paragraph",
        "paragraph",
        "table_row",
    ]
    assert parsed.blocks[-1].metadata == {"cells": ["A", "B"]}
    for block in parsed.blocks[:-1]:
        assert block.metadata == {}


def test_flag_on_plain_document_differs_only_by_document_metadata(tmp_path: Path) -> None:
    path = tmp_path / "plain.docx"
    document = Document()
    document.add_paragraph("Birinci abzas")
    document.add_paragraph("İkinci abzas")
    document.save(str(path))

    off = _parse(path, capture_structure=False)
    on = _parse(path, capture_structure=True)

    # The difference is stated explicitly rather than assumed: only the document metadata key.
    assert off.metadata == {}
    assert set(on.metadata) == {"core_properties"}
    assert _blocks_as_dicts(off) == _blocks_as_dicts(on)


def test_a_list_paragraph_style_alone_is_not_a_list(tmp_path: Path) -> None:
    path = tmp_path / "container.docx"
    document = Document()
    document.add_paragraph("Sadəcə konteyner üslubu", style="List Paragraph")
    document.save(str(path))

    parsed = _parse(path, capture_structure=True)

    assert [block.block_type for block in parsed.blocks] == ["paragraph"]
    assert parsed.blocks[0].metadata == {}


def test_structure_uses_the_recorded_heading_level() -> None:
    blocks = [
        {
            "sequence": 1,
            "block_type": "heading",
            "text": "Ümumi məlumat",
            "locator": {"paragraph_index": 0},
            "metadata": {"level": 3, "level_source": "outline"},
        },
        {
            "sequence": 2,
            "block_type": "heading",
            "text": "Detallar",
            "locator": {"paragraph_index": 1},
            "metadata": {"level": 2, "level_source": "style"},
        },
        {
            "sequence": 3,
            "block_type": "paragraph",
            "text": "Mətn",
            "locator": {"paragraph_index": 2},
        },
    ]

    structure = build_document_structure(blocks)

    # Without the recorded level both headings would fall back to level 1 (no numeric prefix).
    assert [section["level"] for section in structure.sections] == [3, 2]
    # The level-3 section ends before the level-2 heading; the level-2 section runs to the end.
    assert structure.sections[0]["block_range"] == [1, 1]
    assert structure.sections[1]["block_range"] == [2, 3]


def test_chunk_ids_are_unchanged_by_list_items(tmp_path: Path) -> None:
    path = tmp_path / "lists.docx"
    document = Document()
    document.add_paragraph("Birinci", style="List Number")
    document.add_paragraph("İkinci", style="List Number")
    document.save(str(path))
    off = _parse(path, capture_structure=False)
    on = _parse(path, capture_structure=True)
    config = ChunkingConfig("v1-medium-1800-180", 1800, 180)

    plain = chunk_normalized_document(
        {"blocks": _blocks_as_dicts(off)}, source_file_id=1, parse_run_id=2, config=config
    )
    structured = chunk_normalized_document(
        {"blocks": _blocks_as_dicts(on)}, source_file_id=1, parse_run_id=2, config=config
    )

    assert [chunk.chunk_id for chunk in plain] == [chunk.chunk_id for chunk in structured]
    assert [chunk.text for chunk in plain] == [chunk.text for chunk in structured]
    assert [chunk.locator for chunk in plain] == [chunk.locator for chunk in structured]
