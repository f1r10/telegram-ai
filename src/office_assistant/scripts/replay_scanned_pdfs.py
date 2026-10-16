"""Operator-controlled queueing of old PDF scans for local OCR.

Dry-run is the default. --apply only queues work: ingestion OCR and vector indexing
run in the normal durable workers, without creating fake Telegram submissions.
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    DocumentParseRun,
    DocumentProcessingStatus,
    FileObject,
    FileObjectState,
    IngestionJob,
    IngestionSessionItem,
    KnowledgeIndexRecord,
    KnowledgeIndexRecordStatus,
    OcrReplayJob,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.scripts.repair_legacy_index import searchable_chunks


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    inspected: int
    eligible: int
    queued: int
    skipped: int


async def latest_deferred_scans(
    session: AsyncSession, *, limit: int
) -> list[tuple[DocumentParseRun, FileObject]]:
    """Use latest parse attempt across *all* sessions of the same physical file."""

    latest = (
        select(func.max(DocumentParseRun.id).label("parse_run_id"))
        .group_by(DocumentParseRun.file_object_id)
        .subquery()
    )
    statement = (
        select(DocumentParseRun, FileObject)
        .join(latest, latest.c.parse_run_id == DocumentParseRun.id)
        .join(FileObject, FileObject.id == DocumentParseRun.file_object_id)
        .join(IngestionJob, IngestionJob.id == DocumentParseRun.ingestion_job_id)
        .join(
            IngestionSessionItem,
            IngestionSessionItem.id == DocumentParseRun.session_item_id,
        )
        .outerjoin(OcrReplayJob, OcrReplayJob.file_object_id == FileObject.id)
        .where(
            DocumentParseRun.status == DocumentProcessingStatus.DEFERRED,
            DocumentParseRun.requires_ocr.is_(True),
            DocumentParseRun.normalized_storage_key.is_not(None),
            FileObject.state == FileObjectState.ACTIVE,
            FileObject.content_type == "application/pdf",
            IngestionSessionItem.session_id == IngestionJob.session_id,
            IngestionSessionItem.file_object_id == FileObject.id,
            OcrReplayJob.id.is_(None),
        )
        .order_by(DocumentParseRun.id)
        .limit(limit)
    )
    return list((await session.execute(statement)).all())


async def already_indexed(session: AsyncSession, file_id: int) -> bool:
    """Never replay a shared file already searchable through another upload session."""

    result = await session.scalar(
        select(KnowledgeIndexRecord.id)
        .join(DocumentParseRun, DocumentParseRun.id == KnowledgeIndexRecord.parse_run_id)
        .where(
            DocumentParseRun.file_object_id == file_id,
            KnowledgeIndexRecord.status == KnowledgeIndexRecordStatus.SUCCEEDED,
            KnowledgeIndexRecord.chunk_count > 0,
        )
        .limit(1)
    )
    return result is not None


def safe_artifacts(
    storage: LocalFileStorage,
    run: DocumentParseRun,
    file_object: FileObject,
    *,
    max_raw_bytes: int,
    chunking: ChunkingConfig,
) -> bool:
    """Only scan files within upload limits and a real zero-chunk normalized artifact."""

    if not file_object.original_filename.lower().endswith(".pdf"):
        return False
    if not run.normalized_storage_key or not file_object.storage_key:
        return False
    try:
        path = storage.path_for_key(file_object.storage_key)
        if not path.is_file() or not (0 < path.stat().st_size <= max_raw_bytes):
            return False
        derived = storage.path_for_key(run.normalized_storage_key)
        return (
            searchable_chunks(
                derived, file_id=file_object.id, run_id=run.id, config=chunking
            )
            == 0
            and derived.is_file()
        )
    except (OSError, ValueError):
        return False


async def queue_legacy_scans(
    settings: Settings, *, apply: bool = False, limit: int = 100
) -> ReplaySummary:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be from 1 to 500")
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    storage = LocalFileStorage(settings.file_storage_path)
    chunking = ChunkingConfig(
        settings.chunking_version, settings.chunk_max_chars, settings.chunk_overlap_chars
    )
    inspected = eligible = queued = skipped = 0
    try:
        async with factory() as session:
            rows = await latest_deferred_scans(session, limit=limit)
        for run, file_object in rows:
            inspected += 1
            if (
                not safe_artifacts(
                    storage, run, file_object, max_raw_bytes=settings.max_upload_bytes,
                    chunking=chunking,
                )
            ):
                skipped += 1
                continue
            async with factory() as session:
                if await already_indexed(session, file_object.id):
                    skipped += 1
                    continue
            eligible += 1
            if not apply:
                continue
            async with factory.begin() as session:
                # Revalidate latest status under row lock: another import may have
                # made this exact content searchable while the operator scanned.
                current = await session.get(DocumentParseRun, run.id, with_for_update=True)
                last_id = await session.scalar(
                    select(func.max(DocumentParseRun.id)).where(
                        DocumentParseRun.file_object_id == file_object.id
                    )
                )
                if (
                    current is None
                    or current.status != DocumentProcessingStatus.DEFERRED
                    or last_id != run.id
                    or await already_indexed(session, file_object.id)
                ):
                    skipped += 1
                    continue
                statement = (
                    insert(OcrReplayJob)
                    .values(
                        file_object_id=file_object.id,
                        source_parse_run_id=run.id,
                        status="PENDING",
                        attempt_count=0,
                    )
                    .on_conflict_do_nothing(index_elements=["file_object_id"])
                    .returning(OcrReplayJob.id)
                )
                if (await session.scalar(statement)) is not None:
                    queued += 1
                else:
                    skipped += 1
        return ReplaySummary(inspected, eligible, queued, skipped)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Queue after reviewed backup")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    try:
        result = asyncio.run(
            queue_legacy_scans(load_settings(), apply=args.apply, limit=args.limit)
        )
    except Exception as error:
        print(f"OCR_REPLAY_STATUS=FAILED error_type={type(error).__name__}")
        raise SystemExit(1) from None
    print(f"OCR_REPLAY_STATUS={'QUEUED' if args.apply else 'DRY_RUN'}")
    print(
        f"inspected={result.inspected} eligible={result.eligible} "
        f"queued={result.queued} skipped={result.skipped}"
    )


if __name__ == "__main__":
    main()
