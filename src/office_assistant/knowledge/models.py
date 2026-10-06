"""Typed chunk and retrieval models independent of Qdrant SDK types."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    version: str
    max_chars: int
    overlap_chars: int
    max_tokens: int = 512


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    chunk_id: str
    source_file_id: int
    parse_run_id: int
    sequence: int
    text: str
    source_text: str
    block_types: tuple[str, ...]
    locator: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())
    chunking_version: str = "v1"
    token_count: int = 0


@dataclass(frozen=True, slots=True)
class RetrievalFilter:
    file_id: int | None = None
    parse_run_id: int | None = None
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    text: str
    source_filename: str
    source_file_id: int
    parse_run_id: int
    locator: dict[str, Any]
    retrieval_method: str
    score: float
    rank: int
    payload: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())
