"""Queue safely verified deferred image OCR jobs; dry run unless --apply."""

import argparse
import asyncio
import logging
from dataclasses import dataclass

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
    OcrReplayJob,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.processing.ocr_replay import verified_raw_image
from office_assistant.scripts.replay_scanned_pdfs import already_indexed


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    inspected: int
    eligible: int
    queued: int
    skipped: int


async def latest_deferred_images(
    session: AsyncSession, *, limit: int
) -> list[tuple[DocumentParseRun, FileObject]]:
    latest = (
        select(func.max(DocumentParseRun.id).label("run_id"))
        .group_by(DocumentParseRun.file_object_id)
        .subquery()
    )
    stmt = (
        select(DocumentParseRun, FileObject)
        .join(latest, latest.c.run_id == DocumentParseRun.id)
        .join(FileObject, FileObject.id == DocumentParseRun.file_object_id)
        .join(IngestionJob, IngestionJob.id == DocumentParseRun.ingestion_job_id)
        .join(IngestionSessionItem, IngestionSessionItem.id == DocumentParseRun.session_item_id)
        .outerjoin(OcrReplayJob, OcrReplayJob.file_object_id == FileObject.id)
        .where(
            DocumentParseRun.status == DocumentProcessingStatus.DEFERRED,
            DocumentParseRun.parser_name == "image-probe",
            DocumentParseRun.requires_vision.is_(True),
            FileObject.state == FileObjectState.ACTIVE,
            FileObject.content_type.in_(["image/png", "image/jpeg"]),
            IngestionSessionItem.session_id == IngestionJob.session_id,
            IngestionSessionItem.file_object_id == FileObject.id,
            OcrReplayJob.id.is_(None),
        )
        .order_by(DocumentParseRun.id)
        .limit(limit)
    )
    return [(run, file) for run, file in (await session.execute(stmt)).all()]


async def queue_deferred_images(
    settings: Settings, *, apply: bool = False, limit: int = 25
) -> ReplaySummary:
    if not settings.image_ocr_enabled or not 1 <= limit <= 100:
        raise ValueError("Image OCR must be enabled and limit must be 1-100")
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    storage = LocalFileStorage(settings.file_storage_path)
    inspected = eligible = queued = skipped = 0
    try:
        async with factory() as session:
            records = await latest_deferred_images(session, limit=limit)
        for run, file in records:
            inspected += 1
            if (
                not run.normalized_storage_key
                or verified_raw_image(storage, file, settings.max_upload_bytes) is None
            ):
                skipped += 1
                continue
            async with factory() as session:
                if await already_indexed(session, file.id):
                    skipped += 1
                    continue
            eligible += 1
            if not apply:
                continue
            async with factory.begin() as session:
                current = await session.get(DocumentParseRun, run.id, with_for_update=True)
                latest_id = await session.scalar(
                    select(func.max(DocumentParseRun.id))
                    .where(DocumentParseRun.file_object_id == file.id)
                )
                if (
                    current is None
                    or current.status != DocumentProcessingStatus.DEFERRED
                    or latest_id != run.id
                    or await already_indexed(session, file.id)
                ):
                    skipped += 1
                    continue
                record = await session.scalar(
                    insert(OcrReplayJob)
                    .values(
                        file_object_id=file.id,
                        source_parse_run_id=run.id,
                        status="PENDING",
                        attempt_count=0,
                    )
                    .on_conflict_do_nothing(index_elements=["file_object_id"])
                    .returning(OcrReplayJob.id)
                )
                if record is not None:
                    queued += 1
                else:
                    skipped += 1
        return ReplaySummary(inspected, eligible, queued, skipped)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Queue eligible OCR replays")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    try:
        summary = asyncio.run(
            queue_deferred_images(load_settings(), apply=args.apply, limit=args.limit)
        )
    except Exception as error:
        print(f"IMAGE_OCR_REPLAY=FAILED error_type={type(error).__name__}")
        raise SystemExit(1) from None
    print(f"IMAGE_OCR_REPLAY={'QUEUED' if args.apply else 'DRY_RUN'}")
    print(
        f"inspected={summary.inspected} eligible={summary.eligible} "
        f"queued={summary.queued} skipped={summary.skipped}"
    )


if __name__ == "__main__":
    main()
