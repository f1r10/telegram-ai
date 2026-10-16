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

from sqlalchemy import select

from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    DocumentParseRun,
    DocumentProcessingStatus,
    KnowledgeIndexJob,
    KnowledgeIndexJobStatus,
    KnowledgeIndexRecord,
    KnowledgeIndexRecordStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.knowledge.chunking import chunk_normalized_artifact
from office_assistant.knowledge.models import ChunkingConfig


@dataclass(frozen=True, slots=True)
class RepairSummary:
    scanned: int = 0
    eligible: int = 0
    applied: int = 0
    skipped: int = 0


def searchable_chunks(path: Path, *, file_id: int, run_id: int, config: ChunkingConfig) -> int:
    """Use the production chunker, not an approximate 'has blocks' check."""

    if not path.is_file():
        return 0
    try:
        return len(
            chunk_normalized_artifact(
                path, source_file_id=file_id, parse_run_id=run_id, config=config
            )
        )
    except (OSError, ValueError, KeyError, TypeError):
        return 0


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
            rows = (
                await session.execute(
                    select(
                        DocumentParseRun.id,
                        DocumentParseRun.ingestion_job_id,
                        DocumentParseRun.file_object_id,
                        DocumentParseRun.normalized_storage_key,
                    )
                    .where(
                        DocumentParseRun.status == DocumentProcessingStatus.DEFERRED,
                        DocumentParseRun.normalized_storage_key.is_not(None),
                    )
                    .order_by(DocumentParseRun.id)
                    .limit(limit)
                )
            ).all()
        for run_id, ingestion_job_id, file_id, key in rows:
            scanned += 1
            if not key:
                skipped += 1
                continue
            path = storage.path_for_key(key)
            if searchable_chunks(path, file_id=file_id, run_id=run_id, config=config) == 0:
                skipped += 1
                continue
            eligible += 1
            if not apply:
                continue
            async with factory.begin() as session:
                run = await session.get(DocumentParseRun, run_id, with_for_update=True)
                if run is None or run.status != DocumentProcessingStatus.DEFERRED:
                    skipped += 1
                    continue
                job = await session.scalar(
                    select(KnowledgeIndexJob)
                    .where(KnowledgeIndexJob.ingestion_job_id == ingestion_job_id)
                    .with_for_update()
                )
                if job is not None and job.status == KnowledgeIndexJobStatus.RUNNING:
                    skipped += 1
                    continue
                run.status = DocumentProcessingStatus.SUCCEEDED
                if job is None:
                    job = KnowledgeIndexJob(
                        ingestion_job_id=ingestion_job_id,
                        status=KnowledgeIndexJobStatus.PENDING,
                        collection_alias=settings.qdrant_collection_alias,
                        collection_name=settings.qdrant_collection_name,
                        embedding_model=settings.embedding_model,
                        chunking_version=settings.chunking_version,
                    )
                    session.add(job)
                else:
                    # Already valid nonempty records remain untouched; only false
                    # zero-chunk successes must be reprocessed by the worker.
                    records = (
                        (
                            await session.execute(
                                select(KnowledgeIndexRecord).where(
                                    KnowledgeIndexRecord.knowledge_index_job_id == job.id
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    for record in records:
                        if (
                            record.status == KnowledgeIndexRecordStatus.SUCCEEDED
                            and record.chunk_count == 0
                        ):
                            record.status = KnowledgeIndexRecordStatus.FAILED
                            record.last_error = "Legacy zero-chunk success queued for repair"
                    job.status = KnowledgeIndexJobStatus.PENDING
                    job.attempt_count = 0
                    job.available_at = datetime.now(UTC)
                    job.started_at = None
                    job.finished_at = None
                    job.locked_at = None
                    job.lease_expires_at = None
                    job.worker_id = None
                    job.last_error = None
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
