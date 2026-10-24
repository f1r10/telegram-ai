"""Durable replay worker for historical deferred scanned PDFs.

The original upload, session, and parse history are immutable. Only a new parse
attempt and its existing ingestion job's knowledge-index queue may be created.
Structured extraction, sales and Telegram submission paths are never replayed.
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.core.settings import Settings
from office_assistant.infrastructure.database.models import (
    DocumentParseRun,
    DocumentProcessingStatus,
    FileObject,
    FileObjectState,
    KnowledgeIndexJob,
    KnowledgeIndexJobStatus,
    OcrReplayJob,
)
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.knowledge.chunking import chunk_normalized_document
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.processing.image_ocr import MAX_INPUT_BYTES, ImageOcrService
from office_assistant.processing.models import ParsedDocument
from office_assistant.processing.parsers import ParserRegistry
from office_assistant.processing.pdf_ocr import PdfOcrConfig, PdfOcrService
from office_assistant.processing.worker_service import searchable_parse_status

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReplayClaim:
    job_id: int
    worker_id: str
    attempt: int


def verified_raw_image(storage: LocalFileStorage, file: FileObject, cap: int) -> Path | None:
    """Reject replaced, symlinked, over-budget and mislabeled image source files."""
    if file.state != FileObjectState.ACTIVE or file.content_type not in {
        "image/jpeg",
        "image/png",
    }:
        return None
    extension = Path(file.original_filename).suffix.lower()
    if (file.content_type == "image/png" and extension != ".png") or (
        file.content_type == "image/jpeg" and extension not in {".jpg", ".jpeg"}
    ):
        return None
    path = storage.path_for_key(file.storage_key)
    if path.is_symlink() or not path.is_file():
        return None
    size = path.stat().st_size
    if not 0 < size <= min(cap, MAX_INPUT_BYTES):
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        header = stream.read(12)
        if (file.content_type == "image/png" and not header.startswith(b"\x89PNG\r\n\x1a\n")) or (
            file.content_type == "image/jpeg" and not header.startswith(b"\xff\xd8\xff")
        ):
            return None
        stream.seek(0)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return path if digest.hexdigest() == file.sha256 else None


def verified_raw_pdf(storage: LocalFileStorage, file: FileObject, cap: int) -> Path | None:
    """Never process missing, replaced, oversized or non-PDF source bytes."""

    if file.state != FileObjectState.ACTIVE or file.content_type != "application/pdf":
        return None
    if not file.original_filename.lower().endswith(".pdf"):
        return None
    path = storage.path_for_key(file.storage_key)
    if not path.is_file() or not 0 < path.stat().st_size <= cap:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if not stream.read(5).startswith(b"%PDF-"):
            return None
        stream.seek(0)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != file.sha256:
        return None
    return path


class OcrReplayService:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        settings: Settings,
        *,
        registry: ParserRegistry | None = None,
    ) -> None:
        self.factory = factory
        self.storage = storage
        self.settings = settings
        self.lease_seconds = settings.worker_lease_seconds
        self.max_attempts = settings.worker_max_attempts
        self.registry = registry or ParserRegistry(
            image_ocr=(
                ImageOcrService(languages=settings.pdf_ocr_languages)
                if settings.image_ocr_enabled
                else None
            ),
            pdf_ocr=PdfOcrService(
                PdfOcrConfig(
                    languages=settings.pdf_ocr_languages,
                    max_pages=settings.pdf_ocr_max_pages,
                    page_timeout_seconds=settings.pdf_ocr_page_timeout_seconds,
                    document_timeout_seconds=min(
                        settings.pdf_ocr_document_timeout_seconds,
                        max(1, settings.worker_lease_seconds - 60),
                    ),
                )
            )
        )
        self.chunking = ChunkingConfig(
            settings.chunking_version, settings.chunk_max_chars, settings.chunk_overlap_chars
        )

    async def claim_next(self, worker_id: str) -> ReplayClaim | None:
        now = datetime.now(UTC)
        async with self.factory.begin() as session:
            while True:
                job = await session.scalar(
                    select(OcrReplayJob)
                    .where(
                        ((OcrReplayJob.status == "PENDING") & (OcrReplayJob.available_at <= now))
                        | (
                            (OcrReplayJob.status == "RUNNING")
                            & (OcrReplayJob.lease_expires_at <= now)
                        )
                    )
                    .order_by(OcrReplayJob.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if job is None:
                    return None
                if job.attempt_count >= self.max_attempts:
                    job.status = "FAILED"
                    job.last_error = "OCR_MAX_ATTEMPTS_EXCEEDED"
                    job.finished_at = now
                    job.locked_at = None
                    job.lease_expires_at = None
                    job.worker_id = None
                    continue
                job.status = "RUNNING"
                job.attempt_count += 1
                job.worker_id = worker_id
                job.locked_at = now
                job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                job.last_error = None
                return ReplayClaim(job.id, worker_id, job.attempt_count)

    async def process(self, claim: ReplayClaim) -> None:
        try:
            await self._process(claim)
        except Exception as error:
            await self._retry_or_fail(claim, type(error).__name__)
            logger.warning(
                "OCR replay attempt failed | job_id=%s kind=%s",
                claim.job_id,
                type(error).__name__,
            )

    @staticmethod
    def _owns(job: OcrReplayJob | None, claim: ReplayClaim) -> bool:
        return (
            job is not None
            and job.status == "RUNNING"
            and job.worker_id == claim.worker_id
            and job.attempt_count == claim.attempt
        )

    async def _source(
        self, session: AsyncSession, job: OcrReplayJob
    ) -> tuple[DocumentParseRun, FileObject] | None:
        original = await session.get(DocumentParseRun, job.source_parse_run_id)
        file = await session.get(FileObject, job.file_object_id)
        if original is None or file is None:
            return None
        last_id = await session.scalar(
            select(func.max(DocumentParseRun.id)).where(
                DocumentParseRun.file_object_id == job.file_object_id
            )
        )
        if (
            original.file_object_id != file.id
            or original.status != DocumentProcessingStatus.DEFERRED
            or not (
                (original.requires_ocr and file.content_type == "application/pdf")
                or (
                    original.requires_vision
                    and original.parser_name == "image-probe"
                    and file.content_type in {"image/png", "image/jpeg"}
                )
            )
            or not original.session_item_id
            or last_id != original.id
        ):
            return None
        return original, file

    async def _process(self, claim: ReplayClaim) -> None:
        async with self.factory() as session:
            job = await session.get(OcrReplayJob, claim.job_id)
            if not self._owns(job, claim):
                return
            assert job is not None
            source = await self._source(session, job)
            if source is None:
                await self._skip(claim, "OCR_SOURCE_NO_LONGER_ELIGIBLE")
                return
            _, file = source
            path = (
                verified_raw_pdf(self.storage, file, self.settings.max_upload_bytes)
                if file.content_type == "application/pdf"
                else verified_raw_image(self.storage, file, self.settings.max_upload_bytes)
            )
            if path is None:
                await self._skip(claim, "OCR_SOURCE_MISSING_OR_CHANGED")
                return
            file_id = file.id
            media_type = file.content_type
            filename = file.original_filename

        parser = self.registry.for_filename(filename)
        parsed = await asyncio.to_thread(
            parser.parse, path, source_file_id=file_id, media_type=media_type
        )
        await self._commit(claim, parsed)

    async def _commit(self, claim: ReplayClaim, parsed: ParsedDocument) -> None:
        now = datetime.now(UTC)
        async with self.factory.begin() as session:
            job = await session.get(OcrReplayJob, claim.job_id, with_for_update=True)
            if not self._owns(job, claim):
                return
            assert job is not None
            source = await self._source(session, job)
            if source is None:
                self._finish(job, "SKIPPED", "OCR_SOURCE_NO_LONGER_ELIGIBLE", now)
                return
            original, file = source
            index_job = await session.scalar(
                select(KnowledgeIndexJob)
                .where(KnowledgeIndexJob.ingestion_job_id == original.ingestion_job_id)
                .with_for_update()
            )
            if index_job is not None and index_job.status == KnowledgeIndexJobStatus.RUNNING:
                self._defer(job, now, "INDEXER_BUSY")
                return
            status = searchable_parse_status(parsed)
            if status != DocumentProcessingStatus.SUCCEEDED:
                self._finish(job, "FAILED", "OCR_NO_INDEXABLE_TEXT", now)
                return
            payload = parsed.as_dict()
            payload["ocr_replay"] = {
                "source_parse_run_id": original.id,
                "replay_job_id": job.id,
            }
            chunks = chunk_normalized_document(
                payload,
                source_file_id=file.id,
                parse_run_id=original.id,
                config=self.chunking,
            )
            if not chunks or not any(chunk.text.strip() for chunk in chunks):
                self._finish(job, "FAILED", "OCR_NO_INDEXABLE_CHUNKS", now)
                return
            run = DocumentParseRun(
                file_object_id=file.id,
                ingestion_job_id=original.ingestion_job_id,
                session_item_id=original.session_item_id,
                status=DocumentProcessingStatus.SUCCEEDED,
                parser_name=parsed.parser_name,
                parser_version=parsed.parser_version,
                requires_ocr=parsed.requires_ocr,
                requires_vision=parsed.requires_vision,
                warning_count=len(parsed.warnings),
                started_at=now,
                finished_at=now,
            )
            session.add(run)
            await session.flush()
            run.normalized_storage_key = self.storage.write_derived_json(file.id, run.id, payload)
            run.text_storage_key = self.storage.write_derived_text(file.id, run.id, parsed.text())
            job.result_parse_run_id = run.id
            self._finish(job, "INDEX_QUEUED", None, now)

            if index_job is None:
                session.add(
                    KnowledgeIndexJob(
                        ingestion_job_id=original.ingestion_job_id,
                        status=KnowledgeIndexJobStatus.PENDING,
                        collection_alias=self.settings.qdrant_collection_alias,
                        collection_name=self.settings.qdrant_collection_name,
                        embedding_model=self.settings.embedding_model,
                        chunking_version=self.settings.chunking_version,
                    )
                )
            else:
                index_job.status = KnowledgeIndexJobStatus.PENDING
                index_job.attempt_count = 0
                index_job.available_at = now
                index_job.started_at = None
                index_job.finished_at = None
                index_job.last_error = None
                index_job.locked_at = None
                index_job.lease_expires_at = None
                index_job.worker_id = None

    @staticmethod
    def _finish(job: OcrReplayJob, status: str, reason: str | None, now: datetime) -> None:
        job.status = status
        job.last_error = reason
        job.finished_at = now
        job.locked_at = None
        job.lease_expires_at = None
        job.worker_id = None

    @staticmethod
    def _defer(job: OcrReplayJob, now: datetime, reason: str) -> None:
        job.status = "PENDING"
        job.last_error = reason
        job.available_at = now + timedelta(seconds=30)
        job.attempt_count -= 1
        job.locked_at = None
        job.lease_expires_at = None
        job.worker_id = None

    async def _skip(self, claim: ReplayClaim, reason: str) -> None:
        async with self.factory.begin() as session:
            job = await session.get(OcrReplayJob, claim.job_id, with_for_update=True)
            if self._owns(job, claim):
                assert job is not None
                self._finish(job, "SKIPPED", reason, datetime.now(UTC))

    async def _retry_or_fail(self, claim: ReplayClaim, failure: str) -> None:
        async with self.factory.begin() as session:
            job = await session.get(OcrReplayJob, claim.job_id, with_for_update=True)
            if not self._owns(job, claim):
                return
            assert job is not None
            now = datetime.now(UTC)
            if claim.attempt >= self.max_attempts:
                self._finish(job, "FAILED", "OCR_RUNTIME_" + failure[:80], now)
            else:
                self._defer(job, now, "OCR_RUNTIME_" + failure[:80])
                # Runtime failures consume attempts; only an already-running
                # indexer uses the free retry permitted by _defer.
                job.attempt_count += 1
                job.available_at = now + timedelta(seconds=min(120, 2**claim.attempt))
