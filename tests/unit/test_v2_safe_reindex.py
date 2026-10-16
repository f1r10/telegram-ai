"""Legacy reindex is opt-in and refuses unreadable or zero-chunk artifacts."""

import json
from pathlib import Path

from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.scripts.repair_legacy_index import searchable_chunks


def _write(path: Path, blocks: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps({"schema_version": "v1", "blocks": blocks}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_repair_uses_real_chunker_only_for_existing_searchable_text(tmp_path: Path) -> None:
    config = ChunkingConfig("v1-medium-1800-180", 1800, 180)
    eligible = tmp_path / "text.json"
    empty = tmp_path / "empty.json"
    scanned = tmp_path / "scanned.json"
    malformed = tmp_path / "malformed.json"
    _write(
        eligible,
        [{"block_type": "page_text", "text": "KA45 zəmanəti 24 aydır.", "locator": {"page": 1}}],
    )
    _write(empty, [])
    _write(
        scanned,
        [{"block_type": "page_text", "text": "    ", "locator": {"page": 2}}],
    )
    malformed.write_text("not-json", encoding="utf-8")
    assert searchable_chunks(eligible, file_id=21, run_id=2, config=config) == 1
    assert searchable_chunks(empty, file_id=22, run_id=3, config=config) == 0
    assert searchable_chunks(scanned, file_id=23, run_id=4, config=config) == 0
    assert searchable_chunks(malformed, file_id=24, run_id=5, config=config) == 0
    assert searchable_chunks(tmp_path / "missing", file_id=25, run_id=6, config=config) == 0
