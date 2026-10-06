"""Small normalized document model shared by all parser adapters."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ContentBlock:
    sequence: int
    block_type: str
    text: str
    locator: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())
    metadata: dict[str, Any] = field(default_factory=lambda: dict[str, Any]())


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    source_file_id: int
    parser_name: str
    parser_version: str
    media_type: str
    metadata: dict[str, Any]
    blocks: list[ContentBlock]
    warnings: list[str] = field(default_factory=lambda: list[str]())
    requires_vision: bool = False
    requires_ocr: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks if block.text)
