import json
from pathlib import Path

from office_assistant.knowledge.chunking import chunk_normalized_artifact, stable_chunk_id
from office_assistant.knowledge.models import ChunkingConfig


def _document() -> dict[str, object]:
    return {
        "blocks": [
            {
                "sequence": 1,
                "block_type": "heading",
                "text": "Zəmanət",
                "locator": {"page": 2},
            },
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


def test_chunking_is_deterministic_and_keeps_provenance(tmp_path: Path) -> None:
    path = tmp_path / "normalized.json"
    path.write_text(json.dumps(_document(), ensure_ascii=False), encoding="utf-8")
    config = ChunkingConfig("v1-test", 1000, 50)

    first = chunk_normalized_artifact(path, source_file_id=10, parse_run_id=20, config=config)
    second = chunk_normalized_artifact(path, source_file_id=10, parse_run_id=20, config=config)

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert first[0].locator["blocks"] == [
        {"page": 2},
        {"page": 2},
        {"sheet": "Products", "range": "A42:C42"},
    ]
    assert "Sheet: Products" in first[0].text
    assert "ka45" in first[0].metadata["identifiers"]


def test_long_blocks_are_split_without_exceeding_limit(tmp_path: Path) -> None:
    path = tmp_path / "normalized.json"
    document = {
        "blocks": [
            {"block_type": "plain_text", "text": "word " * 1000, "locator": {"line_start": 1}}
        ]
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    chunks = chunk_normalized_artifact(
        path,
        source_file_id=1,
        parse_run_id=2,
        config=ChunkingConfig("v1-small", 300, 30),
    )

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 300 + 30 for chunk in chunks)
    assert stable_chunk_id(1, 2, "v1-small", 1, {"x": 1}) == stable_chunk_id(
        1, 2, "v1-small", 1, {"x": 1}
    )
