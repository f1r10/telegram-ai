"""Safe, opt-in reindex for historical partially readable PDFs skipped as DEFERRED.

Default: dry-run. No upload bytes, customer data or vectors are changed unless
--apply is explicitly provided. The existing worker performs actual indexing.
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    DocumentParseRun,
    FileObject,
    IngestionJob,
    IngestionSessionItem,
    OcrReplayJob,
    DocumentProcessingStatus,
    KnowledgeIndexJob,
    KnowledgeIndexJobStatus,
    KnowledgeIndexRecord,
    KnowledgeIndexRecordStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage, StorageError
from office_assistant.knowledge.chunking import chunk_normalized_artifact
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.processing.ocr_replay import verified_raw_pdf


@dataclass(frozen=True, slots=True)
class RepairSummary:
    scanned: int = 0
    eligible: int = 0
    applied: int = 0
    skipped: int = 0


def searchable_chunks(path: Path, *, file_id: int, run_id: int, config: ChunkingConfig) -> int:
    """Use the production chunker, not an approximate 'has blocks' check."""

    if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= 32 * 1024 * 1024:
        return 0
    try:
        return len(
            chunk_normalized_artifact(
                path, source_file_id=file_id, parse_run_id=run_id, config=config
            )
        )
    except (OSError, ValueError, KeyError, TypeError):
        return 0


async def _verified_candidate(
    session: AsyncSession,
    run: DocumentParseRun,
    storage: LocalFileStorage,
    settings: Settings,
    chunking: ChunkingConfig,
) -> bool:
    """Recheck source lineage, integrity and successful newer indexing before any write."""
    if (
        run.status != DocumentProcessingStatus.DEFERRED
        or not run.normalized_storage_key
        or run.session_item_id is None
    ):
        return False
    file = await session.get(FileObject, run.file_object_id, with_for_update=True)
    item = await session.get(IngestionSessionItem, run.session_item_id)
    ingestion = await session.get(IngestionJob, run.ingestion_job_id)
    if (
        file is None
        or item is None
        or ingestion is None
        or item.file_object_id != file.id
        or item.session_id != ingestion.session_id
    ):
        return False
    latest = await session.scalar(
        select(func.max(DocumentParseRun.id)).where(
            DocumentParseRun.file_object_id == file.id
        )
    )
    if latest != run.id:
        return False
    # A separate OCR-replay job owns the source; do not race its worker or
    # override an already reviewed OCR result with partially readable text.
    replay = await session.scalar(
        select(OcrReplayJob.id).where(OcrReplayJob.file_object_id == file.id).limit(1)
    )
    if replay is not None:
        return False
    already_good = await session.scalar(
        select(KnowledgeIndexRecord.id)
        .join(DocumentParseRun, DocumentParseRun.id == KnowledgeIndexRecord.parse_run_id)
        .where(
            DocumentParseRun.file_object_id == file.id,
            KnowledgeIndexRecord.status == KnowledgeIndexRecordStatus.SUCCEEDED,
            KnowledgeIndexRecord.chunk_count > 0,
        )
        .limit(1)
    )
    if already_good is not None:
        return False
    try:
        if verified_raw_pdf(storage, file, settings.max_upload_bytes) is None:
            return False
        artifact = storage.path_for_key(run.normalized_storage_key)
        return searchable_chunks(
            artifact, file_id=file.id, run_id=run.id, config=chunking
        ) > 0
    except (OSError, StorageError, ValueError, TypeError):
        return False


async def repair(settings: Settings, *, apply: bool, limit: int = 100) -> RepairSummary:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be from 1 to 500")
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    storage = LocalFileStorage(settings.file_storage_path)
    config = ChunkingConfig(
        settings.chunking_version, settings.chunk_max_chars, settings.chunk_overlap_chars
    )
    scanned = eligible = applied = skipped = 0
    try:
        async with factory() as session:
            ids = list(
                (
                    await session.scalars(
                        select(DocumentParseRun.id)
                        .where(
                            DocumentParseRun.status == DocumentProcessingStatus.DEFERRED,
                            DocumentParseRun.normalized_storage_key.is_not(None),
                        )
                        .order_by(DocumentParseRun.id)
                        .limit(limit)
                    )
                ).all()
            )
        for run_id in ids:
            scanned += 1
            async with factory.begin() as session:
                run = await session.get(DocumentParseRun, run_id, with_for_update=apply)
                if run is None or not await _verified_candidate(
                    session, run, storage, settings, config
                ):
                    skipped += 1
                    continue
                job = await session.scalar(
                    select(KnowledgeIndexJob)
                    .where(KnowledgeIndexJob.ingestion_job_id == run.ingestion_job_id)
                    .with_for_update() if apply else select(KnowledgeIndexJob).where(
                        KnowledgeIndexJob.ingestion_job_id == run.ingestion_job_id
                    )
                )
                if job is not None and job.status == KnowledgeIndexJobStatus.RUNNING:
                    skipped += 1
                    continue
                # Never touch a sibling's completed vector record, or a worker
                # that is independently processing the target record.
                target = None
                if job is not None:
                    target = await session.scalar(
                        select(KnowledgeIndexRecord).where(
                            KnowledgeIndexRecord.knowledge_index_job_id == job.id,
                            KnowledgeIndexRecord.parse_run_id == run.id,
                        )
                    )
                    if target is not None and (
                        target.status == KnowledgeIndexRecordStatus.RUNNING
                        or (
                            target.status == KnowledgeIndexRecordStatus.SUCCEEDED
                            and target.chunk_count > 0
                        )
                    ):
                        skipped += 1
                        continue
                eligible += 1
                if not apply:
                    continue
                # Verify after acquiring the index lock too: a parallel indexer
                # can have completed this source during the operator's scan.
                if not await _verified_candidate(session, run, storage, settings, config):
                    eligible -= 1
                    skipped += 1
                    continue
                run.status = DocumentProcessingStatus.SUCCEEDED
                if job is None:
                    job = KnowledgeIndexJob(
                        ingestion_job_id=run.ingestion_job_id,
                        status=KnowledgeIndexJobStatus.PENDING,
                        collection_alias=settings.qdrant_collection_alias,
                        collection_name=settings.qdrant_collection_name,
                        embedding_model=settings.embedding_model,
                        chunking_version=settings.chunking_version,
                    )
                    session.add(job)
                else:
                    if (
                        target is not None
                        and target.status == KnowledgeIndexRecordStatus.SUCCEEDED
                        and target.chunk_count == 0
                    ):
                        target.status = KnowledgeIndexRecordStatus.FAILED
                        target.indexed_at = None
                        target.last_error = "Legacy zero-chunk target queued for repair"
                    job.status = KnowledgeIndexJobStatus.PENDING
                    job.attempt_count = 0
                    job.available_at = datetime.now(UTC)
                    job.started_at = None
                    job.finished_at = None
                    job.locked_at = None
                    job.lease_expires_at = None
                    job.worker_id = None
                    job.last_error = None
                session.add(
                    AuditEvent(
                        actor_user_id=None,
                        action="LEGACY_REINDEX_OPERATOR_QUEUED",
                        entity_type="document_parse_run",
                        entity_id=str(run.id),
                        event_metadata={
                            "source_parse_run_id": run.id,
                            "ingestion_job_id": run.ingestion_job_id,
                        },
                    )
                )
                applied += 1
        return RepairSummary(scanned, eligible, applied, skipped)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Queue verified legacy docs for indexing"
    )
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    try:
        result = asyncio.run(repair(load_settings(), apply=args.apply, limit=args.limit))
    except Exception as error:
        # File paths and credentials must not leak into command output.
        print(f"REINDEX_STATUS=FAILED error_type={type(error).__name__}")
        raise SystemExit(1) from None
    print(f"REINDEX_STATUS={'APPLIED' if args.apply else 'DRY_RUN'}")
    print(
        f"scanned={result.scanned} eligible={result.eligible} "
        f"queued={result.applied} skipped={result.skipped}"
    )


if __name__ == "__main__":
    main()
