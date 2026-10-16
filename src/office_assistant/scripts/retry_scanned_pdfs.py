"""Controlled retries for terminal OCR failures and incomplete Qdrant projections.

No changes by default. The operator must specify one failure phase and --apply.
Old parse runs are immutable; operator retries are audit events, not new uploads.
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    DocumentParseRun,
    DocumentProcessingStatus,
    FileObject,
    IngestionJob,
    IngestionSessionItem,
    KnowledgeIndexJob,
    KnowledgeIndexJobStatus,
    KnowledgeIndexRecord,
    KnowledgeIndexRecordStatus,
    OcrReplayJob,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage, StorageError
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.processing.ocr_replay import verified_raw_pdf
from office_assistant.scripts.replay_scanned_pdfs import already_indexed, safe_artifacts
from office_assistant.scripts.repair_legacy_index import searchable_chunks

RetryKind = Literal["ocr", "index"]


@dataclass(frozen=True, slots=True)
class RetrySummary:
    inspected: int = 0
    eligible: int = 0
    queued: int = 0
    skipped: int = 0


async def _source_is_valid(
    session: AsyncSession,
    replay: OcrReplayJob,
    storage: LocalFileStorage,
    settings: Settings,
) -> tuple[DocumentParseRun, FileObject] | None:
    source = await session.get(DocumentParseRun, replay.source_parse_run_id)
    file = await session.get(FileObject, replay.file_object_id)
    if (
        source is None
        or file is None
        or source.file_object_id != file.id
        or source.status != DocumentProcessingStatus.DEFERRED
        or not source.requires_ocr
        or source.session_item_id is None
    ):
        return None
    item = await session.get(IngestionSessionItem, source.session_item_id)
    ingestion = await session.get(IngestionJob, source.ingestion_job_id)
    if (
        item is None
        or ingestion is None
        or item.session_id != ingestion.session_id
        or item.file_object_id != file.id
    ):
        return None
    try:
        if verified_raw_pdf(storage, file, settings.max_upload_bytes) is None:
            return None
    except (OSError, StorageError, ValueError):
        return None
    if await already_indexed(session, file.id):
        return None
    return source, file


async def _eligible_ocr(
    session: AsyncSession,
    replay: OcrReplayJob,
    storage: LocalFileStorage,
    settings: Settings,
    chunking: ChunkingConfig,
) -> bool:
    if replay.status != "FAILED" or replay.result_parse_run_id is not None:
        return False
    source = await _source_is_valid(session, replay, storage, settings)
    if source is None:
        return False
    original, file = source
    last_id = await session.scalar(
        select(func.max(DocumentParseRun.id)).where(
            DocumentParseRun.file_object_id == file.id
        )
    )
    return (
        last_id == original.id
        and safe_artifacts(
            storage, original, file, max_raw_bytes=settings.max_upload_bytes, chunking=chunking
        )
    )


async def _eligible_index(
    session: AsyncSession,
    replay: OcrReplayJob,
    storage: LocalFileStorage,
    settings: Settings,
    chunking: ChunkingConfig,
    *,
    index: KnowledgeIndexJob | None = None,
) -> bool:
    if replay.status != "INDEX_QUEUED" or replay.result_parse_run_id is None:
        return False
    source = await _source_is_valid(session, replay, storage, settings)
    result = await session.get(DocumentParseRun, replay.result_parse_run_id)
    if source is None or result is None:
        return False
    original, file = source
    if (
        result.file_object_id != file.id
        or result.ingestion_job_id != original.ingestion_job_id
        or result.session_item_id != original.session_item_id
        or result.status != DocumentProcessingStatus.SUCCEEDED
        or not result.normalized_storage_key
    ):
        return False
    last_id = await session.scalar(
        select(func.max(DocumentParseRun.id)).where(
            DocumentParseRun.file_object_id == file.id
        )
    )
    if last_id != result.id:
        return False
    if index is None:
        index = await session.scalar(
            select(KnowledgeIndexJob).where(
                KnowledgeIndexJob.ingestion_job_id == original.ingestion_job_id
            )
        )
    if index is None or index.status not in {
        KnowledgeIndexJobStatus.FAILED,
        KnowledgeIndexJobStatus.PARTIAL,
        KnowledgeIndexJobStatus.SUCCEEDED,
    }:
        return False
    record = await session.scalar(
        select(KnowledgeIndexRecord).where(
            KnowledgeIndexRecord.knowledge_index_job_id == index.id,
            KnowledgeIndexRecord.parse_run_id == result.id,
        )
    )
    if (
        record is not None
        and record.status == KnowledgeIndexRecordStatus.SUCCEEDED
        and record.chunk_count > 0
    ):
        return False
    try:
        artifact = storage.path_for_key(result.normalized_storage_key)
        if not artifact.is_file() or not (0 < artifact.stat().st_size <= 32 * 1024 * 1024):
            return False
        return (
            searchable_chunks(
                artifact, file_id=file.id, run_id=result.id, config=chunking
            )
            > 0
        )
    except (OSError, StorageError, ValueError):
        return False


def _audit(session: AsyncSession, replay: OcrReplayJob, kind: RetryKind) -> None:
    """Append minimal operational history without leaking OCR text or file paths."""

    session.add(
        AuditEvent(
            actor_user_id=None,
            action="OCR_REPLAY_OPERATOR_RETRY",
            entity_type="ocr_replay_job",
            entity_id=str(replay.id),
            event_metadata={
                "phase": kind,
                "previous_status": replay.status,
                "previous_attempts": replay.attempt_count,
                "source_parse_run_id": replay.source_parse_run_id,
                "result_parse_run_id": replay.result_parse_run_id,
            },
        )
    )


async def retry_failed(
    settings: Settings,
    *,
    kind: RetryKind,
    apply: bool = False,
    limit: int = 100,
) -> RetrySummary:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be from 1 to 500")
    if kind not in ("ocr", "index"):
        raise ValueError("unknown retry phase")
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    storage = LocalFileStorage(settings.file_storage_path)
    chunking = ChunkingConfig(
        settings.chunking_version, settings.chunk_max_chars, settings.chunk_overlap_chars
    )
    inspected = eligible = queued = skipped = 0
    try:
        async with factory() as session:
            ids = list(
                (
                    await session.scalars(
                        select(OcrReplayJob.id)
                        .where(
                            OcrReplayJob.status == (
                                "FAILED" if kind == "ocr" else "INDEX_QUEUED"
                            )
                        )
                        .order_by(OcrReplayJob.id)
                        .limit(limit)
                    )
                ).all()
            )
        for replay_id in ids:
            async with factory.begin() as session:
                replay = await session.get(
                    OcrReplayJob, replay_id, with_for_update=apply
                )
                if replay is None:
                    continue
                inspected += 1
                if kind == "ocr":
                    allowed = await _eligible_ocr(
                        session, replay, storage, settings, chunking
                    )
                else:
                    allowed = await _eligible_index(
                        session, replay, storage, settings, chunking
                    )
                if not allowed:
                    skipped += 1
                    continue
                eligible += 1
                if not apply:
                    continue
                if kind == "ocr":
                    _audit(session, replay, kind)
                    replay.status = "PENDING"
                    replay.attempt_count = 0
                    replay.available_at = datetime.now(UTC)
                    replay.finished_at = None
                    replay.last_error = None
                    replay.locked_at = None
                    replay.lease_expires_at = None
                    replay.worker_id = None
                else:
                    original = await session.get(
                        DocumentParseRun, replay.source_parse_run_id
                    )
                    assert original is not None
                    index = await session.scalar(
                        select(KnowledgeIndexJob)
                        .where(
                            KnowledgeIndexJob.ingestion_job_id == original.ingestion_job_id
                        )
                        .with_for_update()
                    )
                    # Re-check after the indexer lock, not just at the initial read:
                    # the indexer may have completed while this operator waited.
                    if not await _eligible_index(
                        session, replay, storage, settings, chunking, index=index
                    ):
                        skipped += 1
                        eligible -= 1
                        continue
                    assert index is not None
                    _audit(session, replay, kind)
                    target = await session.scalar(
                        select(KnowledgeIndexRecord)
                        .where(
                            KnowledgeIndexRecord.knowledge_index_job_id == index.id,
                            KnowledgeIndexRecord.parse_run_id == replay.result_parse_run_id,
                        )
                        .with_for_update()
                    )
                    if (
                        target is not None
                        and target.status == KnowledgeIndexRecordStatus.SUCCEEDED
                        and target.chunk_count == 0
                    ):
                        target.status = KnowledgeIndexRecordStatus.FAILED
                        target.indexed_at = None
                    now = datetime.now(UTC)
                    index.status = KnowledgeIndexJobStatus.PENDING
                    index.attempt_count = 0
                    index.available_at = now
                    index.started_at = None
                    index.finished_at = None
                    index.last_error = None
                    index.locked_at = None
                    index.lease_expires_at = None
                    index.worker_id = None
                queued += 1
        return RetrySummary(inspected, eligible, queued, skipped)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--retry-failed-ocr", action="store_true")
    mode.add_argument("--retry-failed-index", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Queue reviewed retries")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    kind: RetryKind = "ocr" if args.retry_failed_ocr else "index"
    try:
        result = asyncio.run(
            retry_failed(load_settings(), kind=kind, apply=args.apply, limit=args.limit)
        )
    except Exception as error:
        print(f"RETRY_STATUS=FAILED error_type={type(error).__name__}")
        raise SystemExit(1) from None
    print(f"RETRY_STATUS={'QUEUED' if args.apply else 'DRY_RUN'} phase={kind}")
    print(
        f"inspected={result.inspected} eligible={result.eligible} "
        f"queued={result.queued} skipped={result.skipped}"
    )


if __name__ == "__main__":
    main()
