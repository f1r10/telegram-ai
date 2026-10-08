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
class EmbeddedImage:
    """One picture carried inside a document, with the locator that says where it sits.

    The bytes belong to the parse, not to the stored result: ``as_dict`` reports them by size only,
    so a derived JSON file can never grow a copy of the picture it describes (Master Spec §23.3).
    """

    locator: dict[str, Any]
    data: bytes
    content_type: str


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
    embedded_images: list[EmbeddedImage] = field(default_factory=lambda: list[EmbeddedImage]())

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["embedded_images"] = [
            {
                "locator": image.locator,
                "content_type": image.content_type,
                "byte_size": len(image.data),
            }
            for image in self.embedded_images
        ]
        return payload

    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks if block.text)
