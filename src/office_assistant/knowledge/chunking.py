"""Deterministic provenance-aware chunking of Phase 4 normalized artifacts."""

import hashlib
import json
from pathlib import Path
from typing import Any

from office_assistant.knowledge.identifiers import lexical_text
from office_assistant.knowledge.models import ChunkingConfig, DocumentChunk


def chunk_normalized_artifact(
    path: Path, *, source_file_id: int, parse_run_id: int, config: ChunkingConfig
) -> list[DocumentChunk]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return chunk_normalized_document(
        document, source_file_id=source_file_id, parse_run_id=parse_run_id, config=config
    )


def chunk_normalized_document(
    document: dict[str, Any],
    *,
    source_file_id: int,
    parse_run_id: int,
    config: ChunkingConfig,
) -> list[DocumentChunk]:
    chunks: list[DocumentChunk] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    heading_context = ""

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        source_text = "\n\n".join(item["text"] for item in current)
        retrieval_text = _retrieval_text(current, heading_context)
        sequence = len(chunks) + 1
        locator = {"blocks": [item["locator"] for item in current]}
        block_types = tuple(item["block_type"] for item in current)
        chunk_id = stable_chunk_id(source_file_id, parse_run_id, config.version, sequence, locator)
        chunks.append(
            DocumentChunk(
                chunk_id=chunk_id,
                source_file_id=source_file_id,
                parse_run_id=parse_run_id,
                sequence=sequence,
                text=retrieval_text,
                source_text=source_text,
                block_types=block_types,
                locator=locator,
                metadata={"identifiers": _identifiers(retrieval_text)},
                chunking_version=config.version,
                token_count=_token_count(retrieval_text),
            )
        )
        current = []
        current_chars = 0

    for raw in document.get("blocks", []):
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        block_type = str(raw.get("block_type") or "plain_text")
        locator: dict[str, Any] = (
            dict(raw["locator"]) if isinstance(raw.get("locator"), dict) else {}
        )
        item: dict[str, Any] = {"text": text, "block_type": block_type, "locator": locator}
        if block_type == "heading":
            heading_context = text
        if len(text) > config.max_chars:
            flush()
            for piece in _split_text(text, config.max_chars, config.overlap_chars):
                part: dict[str, Any] = {
                    "text": piece,
                    "block_type": block_type,
                    "locator": locator,
                }
                current = [part]
                current_chars = len(piece)
                flush()
            continue
        projected = current_chars + len(text) + (2 if current else 0)
        if current and projected > config.max_chars:
            flush()
        current.append(item)
        current_chars += len(text) + (2 if len(current) > 1 else 0)
    flush()
    return chunks


def stable_chunk_id(
    source_file_id: int,
    parse_run_id: int,
    chunking_version: str,
    sequence: int,
    locator: dict[str, Any],
) -> str:
    material = json.dumps(
        [source_file_id, parse_run_id, chunking_version, sequence, locator],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _retrieval_text(blocks: list[dict[str, Any]], heading_context: str) -> str:
    pieces: list[str] = []
    if heading_context and not any(item["text"] == heading_context for item in blocks):
        pieces.append(f"Heading: {heading_context}")
    for item in blocks:
        locator = item["locator"]
        prefix = ""
        if item["block_type"] == "table_row" and "sheet" in locator:
            prefix = f"Sheet: {locator['sheet']} Range: {locator.get('range', '')}\n"
        pieces.append(prefix + item["text"])
    return lexical_text("\n".join(pieces))


def _split_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    words = text.split()
    pieces: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            pieces.append(current)
            tail = current[-overlap_chars:].strip() if overlap_chars else ""
            current = f"{tail} {word}".strip()
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _identifiers(text: str) -> list[str]:
    from office_assistant.knowledge.identifiers import identifier_aliases

    return identifier_aliases(text)


def _token_count(text: str) -> int:
    return len(text.split())
