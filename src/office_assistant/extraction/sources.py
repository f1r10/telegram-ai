"""Build stable bounded extraction units from application-owned artifacts."""

import json
from dataclasses import dataclass
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import (
    DocumentParseRun,
    DocumentProcessingStatus,
    FileObject,
    IngestionItemType,
    IngestionSessionItem,
)
from office_assistant.infrastructure.storage.local import LocalFileStorage


@dataclass(frozen=True, slots=True)
class ExtractionSource:
    source_id: str
    text: str
    locator: dict[str, object]
    ingestion_session_id: int
    ingestion_session_item_id: int | None = None
    file_id: int | None = None
    parse_run_id: int | None = None


@dataclass(frozen=True, slots=True)
class ExtractionBatchInput:
    number: int
    sources: tuple[ExtractionSource, ...]

    def prompt(self) -> str:
        sections: list[str] = []
        for source in self.sources:
            locator = json.dumps(source.locator, ensure_ascii=False, sort_keys=True)
            sections.append(
                f"[SOURCE {source.source_id}]\nlocator: {locator}\ncontent:\n{source.text}"
            )
        return "\n\n".join(sections)


async def load_extraction_sources(
    session: AsyncSession, storage: LocalFileStorage, ingestion_session_id: int
) -> tuple[list[ExtractionSource], int]:
    items = list(
        (
            await session.execute(
                select(IngestionSessionItem)
                .where(IngestionSessionItem.session_id == ingestion_session_id)
                .order_by(IngestionSessionItem.sequence_no)
            )
        )
        .scalars()
        .all()
    )
    sources: list[ExtractionSource] = []
    deferred = 0
    for item in items:
        if item.item_type == IngestionItemType.INSTRUCTION and item.text_content:
            sources.append(
                ExtractionSource(
                    source_id=f"I{item.id}",
                    text=item.text_content.strip(),
                    locator={
                        "sequence": item.sequence_no,
                        "telegram_message_id": item.telegram_message_id,
                    },
                    ingestion_session_id=ingestion_session_id,
                    ingestion_session_item_id=item.id,
                )
            )
        if item.caption:
            sources.append(
                ExtractionSource(
                    source_id=f"C{item.id}",
                    text=item.caption.strip(),
                    locator={"sequence": item.sequence_no, "kind": "caption"},
                    ingestion_session_id=ingestion_session_id,
                    ingestion_session_item_id=item.id,
                    file_id=item.file_object_id,
                )
            )
        if item.file_object_id is None:
            continue
        run = (
            await session.execute(
                select(DocumentParseRun)
                .where(DocumentParseRun.session_item_id == item.id)
                .order_by(DocumentParseRun.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if run is None or run.status == DocumentProcessingStatus.FAILED:
            continue
        if run.requires_ocr or run.requires_vision:
            deferred += 1
            continue
        if not run.normalized_storage_key:
            continue
        payload = cast(
            dict[str, object],
            json.loads(storage.path_for_key(run.normalized_storage_key).read_text("utf-8")),
        )
        blocks = payload.get("blocks", [])
        if not isinstance(blocks, list):
            continue
        typed_blocks = cast(list[object], blocks)
        file_object = await session.get(FileObject, item.file_object_id)
        for index, raw in enumerate(typed_blocks, start=1):
            if not isinstance(raw, dict):
                continue
            fields = cast(dict[str, object], raw)
            text = str(fields.get("text", "")).strip()
            if not text:
                continue
            locator_raw = fields.get("locator")
            locator = (
                cast(dict[str, object], locator_raw).copy() if isinstance(locator_raw, dict) else {}
            )
            if file_object is not None:
                locator["filename"] = file_object.original_filename
            sources.append(
                ExtractionSource(
                    source_id=f"F{item.file_object_id}R{run.id}B{index}",
                    text=text,
                    locator=locator,
                    ingestion_session_id=ingestion_session_id,
                    ingestion_session_item_id=item.id,
                    file_id=item.file_object_id,
                    parse_run_id=run.id,
                )
            )
    return sources, deferred


def build_batches(
    sources: list[ExtractionSource], *, max_chars: int, max_sources: int
) -> list[ExtractionBatchInput]:
    batches: list[ExtractionBatchInput] = []
    current: list[ExtractionSource] = []
    chars = 0
    for source in sources:
        if current and (len(current) >= max_sources or chars + len(source.text) > max_chars):
            batches.append(ExtractionBatchInput(len(batches) + 1, tuple(current)))
            current, chars = [], 0
        if len(source.text) > max_chars:
            for offset in range(0, len(source.text), max_chars):
                chunk = ExtractionSource(
                    source_id=f"{source.source_id}P{offset // max_chars + 1}",
                    text=source.text[offset : offset + max_chars],
                    locator={**source.locator, "part": offset // max_chars + 1},
                    ingestion_session_id=source.ingestion_session_id,
                    ingestion_session_item_id=source.ingestion_session_item_id,
                    file_id=source.file_id,
                    parse_run_id=source.parse_run_id,
                )
                if current:
                    batches.append(ExtractionBatchInput(len(batches) + 1, tuple(current)))
                    current = []
                batches.append(ExtractionBatchInput(len(batches) + 1, (chunk,)))
            chars = 0
            continue
        current.append(source)
        chars += len(source.text)
    if current:
        batches.append(ExtractionBatchInput(len(batches) + 1, tuple(current)))
    return batches
