"""Deterministic source-pack construction and user-facing provenance labels."""

# Locator payloads originate in parser adapters and intentionally remain JSON-like.
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

from dataclasses import dataclass

from office_assistant.knowledge.models import RetrievedChunk


@dataclass(frozen=True, slots=True)
class SourceReference:
    source_id: str
    number: int
    chunk: RetrievedChunk
    label: str


@dataclass(frozen=True, slots=True)
class SourcePack:
    references: tuple[SourceReference, ...]
    text: str
    estimated_tokens: int


def build_source_pack(
    chunks: list[RetrievedChunk], *, max_sources: int, max_context_tokens: int
) -> SourcePack:
    references: list[SourceReference] = []
    seen: set[tuple[int, str, str]] = set()
    used_tokens = 0
    for chunk in chunks:
        key = (chunk.source_file_id, chunk.source_filename, chunk.text)
        if key in seen:
            continue
        estimated = max(1, len(chunk.text.split()))
        if len(references) >= max_sources or used_tokens + estimated > max_context_tokens:
            break
        seen.add(key)
        number = len(references) + 1
        reference = SourceReference(f"S{number}", number, chunk, _label(chunk))
        references.append(reference)
        used_tokens += estimated
    blocks = [
        f"[SOURCE {reference.source_id}]\nfile: {reference.chunk.source_filename}\n"
        f"locator: {reference.label}\ntext:\n{reference.chunk.text}"
        for reference in references
    ]
    return SourcePack(tuple(references), "\n\n".join(blocks), used_tokens)


def _label(chunk: RetrievedChunk) -> str:
    blocks = chunk.locator.get("blocks", [])
    locator = blocks[0] if blocks and isinstance(blocks[0], dict) else chunk.locator
    if not isinstance(locator, dict):
        return "provenans qeyd olunmayıb"
    if "page" in locator:
        return f"səhifə {locator['page']}"
    if "sheet" in locator:
        suffix = f", sətir {int(locator['row_start'])}" if "row_start" in locator else ""
        return f"{locator['sheet']} vərəqi{suffix}"
    if "paragraph" in locator:
        return f"abzas {locator['paragraph']}"
    if "line_start" in locator:
        end = locator.get("line_end", locator["line_start"])
        return f"sətirlər {locator['line_start']}-{end}"
    return "provenans qeyd olunmayıb"
