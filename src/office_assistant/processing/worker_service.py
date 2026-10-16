"""PostgreSQL claiming and deterministic ingestion-job processing."""

import asyncio
import logging
import os
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.infrastructure.database.models import (
    DocumentParseRun,
    DocumentProcessingStatus,
    ExtractionJobStatus,
    FileObject,
    FileObjectState,
    ImageAssetOrigin,
    IngestionItemType,
    IngestionJob,
    IngestionJobStatus,
    IngestionSession,
    IngestionSessionItem,
    KnowledgeIndexJob,
    KnowledgeIndexJobStatus,
    OutboxEvent,
    OutboxEventStatus,
    StructuredExtractionJob,
    User,
)
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.processing.image_assets import ImageAssetRegistry
from office_assistant.processing.parsers import ImageProbeParser, ParserRegistry
from office_assistant.processing.models import ParsedDocument

logger = logging.getLogger(__name__)


def searchable_parse_status(parsed: ParsedDocument) -> DocumentProcessingStatus:
    """Index the readable portion of a partial OCR document; never claim empty success.

    requires_ocr and warnings remain independently persisted to report missing pages.
    """

    return (
        DocumentProcessingStatus.SUCCEEDED
        if any(block.text.strip() for block in parsed.blocks)
        else DocumentProcessingStatus.DEFERRED
    )


@dataclass(frozen=True, slots=True)
class JobClaim:
    job_id: int
    session_id: int
    attempt: int


class IngestionJobService:
    """Claims jobs safely and persists one isolated result per source file."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        *,
        lease_seconds: int = 300,
        max_attempts: int = 3,
        registry: ParserRegistry | None = None,
        index_collection_alias: str = "document_chunks",
        index_collection_name: str = "document_chunks_v1",
        index_embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        index_chunking_version: str = "v1-medium-1800-180",
        structured_extraction_enabled: bool = False,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.registry = registry or ParserRegistry()
        self.index_collection_alias = index_collection_alias
        self.index_collection_name = index_collection_name
        self.index_embedding_model = index_embedding_model
        self.index_chunking_version = index_chunking_version
        self.structured_extraction_enabled = structured_extraction_enabled

    async def claim_next(self, worker_id: str) -> JobClaim | None:
        now = datetime.now(UTC)
        async with self.session_factory.begin() as session:
            while True:
                statement = (
                    select(IngestionJob)
                    .where(
                        (
                            (IngestionJob.status == IngestionJobStatus.PENDING)
                            & (IngestionJob.available_at <= now)
                        )
                        | (
                            (IngestionJob.status == IngestionJobStatus.RUNNING)
                            & (IngestionJob.lease_expires_at <= now)
                        )
                    )
                    .order_by(IngestionJob.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                job = (await session.execute(statement)).scalar_one_or_none()
                if job is None:
                    return None
                if job.attempt_count >= self.max_attempts:
                    job.status = IngestionJobStatus.FAILED
                    job.finished_at = now
                    job.last_error = "Maximum worker attempts exceeded"
                    job.locked_at = None
                    job.lease_expires_at = None
                    job.worker_id = None
                    continue
                job.attempt_count += 1
                job.status = IngestionJobStatus.RUNNING
                job.started_at = job.started_at or now
                job.locked_at = now
                job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                job.worker_id = worker_id
                job.last_error = None
                return JobClaim(job.id, job.session_id, job.attempt_count)

    async def process(self, claim: JobClaim) -> None:
        started = time.perf_counter()
        try:
            await self._process_files(claim)
        except Exception as error:
            await self._record_worker_failure(claim, error)
            logger.exception(
                "Worker job failed | job_id=%s attempt=%s", claim.job_id, claim.attempt
            )
        else:
            logger.info(
                "Worker job finished | job_id=%s attempt=%s duration_ms=%d",
                claim.job_id,
                claim.attempt,
                int((time.perf_counter() - started) * 1000),
            )

    async def _process_files(self, claim: JobClaim) -> None:
        async with self.session_factory() as session:
            job = await session.get(IngestionJob, claim.job_id)
            if job is None or job.status != IngestionJobStatus.RUNNING:
                return
            ingestion_session = await session.get(IngestionSession, claim.session_id)
            if ingestion_session is None:
                raise RuntimeError("Committed ingestion session is missing")
            items = list(
                (
                    await session.execute(
                        select(IngestionSessionItem)
                        .where(IngestionSessionItem.session_id == claim.session_id)
                        .order_by(IngestionSessionItem.sequence_no)
                    )
                )
                .scalars()
                .all()
            )
            context = self._session_context(items)
            file_items: dict[int, IngestionSessionItem] = {}
            for item in items:
                if item.item_type == IngestionItemType.FILE and item.file_object_id is not None:
                    file_items.setdefault(item.file_object_id, item)

        statuses: dict[int, str] = {}
        for file_id, item in file_items.items():
            existing = await self._latest_result(claim.job_id, file_id)
            if existing is not None and existing.status in {
                DocumentProcessingStatus.SUCCEEDED,
                DocumentProcessingStatus.DEFERRED,
            }:
                statuses[file_id] = existing.status
                continue
            statuses[file_id] = await self._process_one(claim, file_id, item, context)
        await self._complete_job(claim, statuses, ingestion_session.user_id)

    @staticmethod
    def _session_context(items: list[IngestionSessionItem]) -> list[dict[str, object]]:
        return [
            {
                "sequence": item.sequence_no,
                "type": item.item_type,
                "text": item.text_content,
                "caption": item.caption,
                "submitted_filename": item.submitted_filename,
                "file_object_id": item.file_object_id,
            }
            for item in items
        ]

    async def _process_one(
        self,
        claim: JobClaim,
        file_id: int,
        item: IngestionSessionItem,
        context: list[dict[str, object]],
    ) -> str:
        run_id: int
        async with self.session_factory.begin() as session:
            file_object = await session.get(FileObject, file_id)
            if file_object is None or file_object.state != FileObjectState.ACTIVE:
                return await self._failed_result(
                    session, claim, file_id, item.id, "Raw file is missing"
                )
            parser = self.registry.for_filename(file_object.original_filename)
            run = DocumentParseRun(
                file_object_id=file_id,
                ingestion_job_id=claim.job_id,
                session_item_id=item.id,
                status=DocumentProcessingStatus.RUNNING,
                parser_name=parser.name,
                parser_version=parser.version,
                started_at=datetime.now(UTC),
            )
            session.add(run)
            await session.flush()
            run_id = run.id
            path = self.storage.path_for_key(file_object.storage_key)
            media_type = file_object.content_type

        try:
            parsed = await asyncio.to_thread(
                parser.parse, path, source_file_id=file_id, media_type=media_type
            )
            payload = parsed.as_dict()
            payload["session_items"] = context
            normalized_key = self.storage.write_derived_json(file_id, run_id, payload)
            text_key = self.storage.write_derived_text(file_id, run_id, parsed.text())
        except Exception as error:
            message = _bounded_error(error)
            async with self.session_factory.begin() as session:
                run = await session.get(DocumentParseRun, run_id, with_for_update=True)
                if run is not None:
                    run.status = DocumentProcessingStatus.FAILED
                    run.error_message = message
                    run.finished_at = datetime.now(UTC)
            logger.exception("File parser failed | job_id=%s file_id=%s", claim.job_id, file_id)
            return DocumentProcessingStatus.FAILED

        # One blank/scanned page must not prevent other searchable pages from being
        # indexed. Keep the requires_ocr flag and warnings for incomplete pages.
        # Entirely empty / image-only documents remain deferred rather than falsely
        # appearing as "indexed" with zero searchable chunks.
        status = searchable_parse_status(parsed)
        async with self.session_factory.begin() as session:
            run = await session.get(DocumentParseRun, run_id, with_for_update=True)
            if run is None:
                raise RuntimeError("Parse run disappeared before completion")
            run.status = status
            run.parser_name = parsed.parser_name
            run.parser_version = parsed.parser_version
            run.normalized_storage_key = normalized_key
            run.text_storage_key = text_key
            run.requires_ocr = parsed.requires_ocr
            run.requires_vision = parsed.requires_vision
            run.warning_count = len(parsed.warnings)
            run.finished_at = datetime.now(UTC)
            if parsed.parser_name == ImageProbeParser.name:
                # The picture becomes queryable content here, but its bytes stay on the local disk:
                # reading them with a vision model is an on-demand act, not an upload-time one.
                await ImageAssetRegistry(session, self.storage).register(
                    path,
                    origin=ImageAssetOrigin.STANDALONE_UPLOAD,
                    source_file_id=file_id,
                    source_parse_run_id=run_id,
                )
            if parser.embedded_origin is not None and parsed.embedded_images:
                # A picture inside a document is content, never an upload: it has no FileObject row
                # and no copy under blobs/ (Master Spec §23.3).
                registry = ImageAssetRegistry(session, self.storage)
                for image in parsed.embedded_images:
                    await registry.register_bytes(
                        image.data,
                        origin=parser.embedded_origin,
                        source_file_id=file_id,
                        source_parse_run_id=run_id,
                        locator=image.locator,
                    )
        logger.info(
            "File processed | job_id=%s file_id=%s parser=%s status=%s blocks=%s",
            claim.job_id,
            file_id,
            parsed.parser_name,
            status,
            len(parsed.blocks),
        )
        return status

    async def _failed_result(
        self,
        session: AsyncSession,
        claim: JobClaim,
        file_id: int,
        item_id: int,
        message: str,
    ) -> str:
        session.add(
            DocumentParseRun(
                file_object_id=file_id,
                ingestion_job_id=claim.job_id,
                session_item_id=item_id,
                status=DocumentProcessingStatus.FAILED,
                parser_name="unavailable",
                parser_version="1.0",
                error_message=message,
                finished_at=datetime.now(UTC),
            )
        )
        return DocumentProcessingStatus.FAILED

    async def _latest_result(self, job_id: int, file_id: int) -> DocumentParseRun | None:
        async with self.session_factory() as session:
            return (
                await session.execute(
                    select(DocumentParseRun)
                    .where(
                        DocumentParseRun.ingestion_job_id == job_id,
                        DocumentParseRun.file_object_id == file_id,
                    )
                    .order_by(DocumentParseRun.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def _complete_job(self, claim: JobClaim, statuses: dict[int, str], user_id: int) -> None:
        success = {DocumentProcessingStatus.SUCCEEDED, DocumentProcessingStatus.DEFERRED}
        if not statuses or all(status in success for status in statuses.values()):
            final = IngestionJobStatus.SUCCEEDED
        elif any(status in success for status in statuses.values()):
            final = IngestionJobStatus.PARTIAL
        else:
            final = IngestionJobStatus.FAILED
        async with self.session_factory.begin() as session:
            job = await session.get(IngestionJob, claim.job_id, with_for_update=True)
            if job is None:
                return
            job.status = final
            job.finished_at = datetime.now(UTC)
            job.locked_at = None
            job.lease_expires_at = None
            job.worker_id = None
            session.add(
                OutboxEvent(
                    event_type="INGESTION_JOB_COMPLETED",
                    aggregate_type="ingestion_job",
                    aggregate_id=str(claim.job_id),
                    recipient_telegram_id=await self._telegram_id(session, user_id),
                    payload={
                        "job_id": claim.job_id,
                        "status": final,
                        "total": len(statuses),
                        "succeeded": sum(
                            status == DocumentProcessingStatus.SUCCEEDED
                            for status in statuses.values()
                        ),
                        "deferred": sum(
                            status == DocumentProcessingStatus.DEFERRED
                            for status in statuses.values()
                        ),
                        "failed": sum(
                            status == DocumentProcessingStatus.FAILED
                            for status in statuses.values()
                        ),
                    },
                    status=OutboxEventStatus.PENDING,
                )
            )
            existing_index_job = await session.scalar(
                select(KnowledgeIndexJob).where(KnowledgeIndexJob.ingestion_job_id == claim.job_id)
            )
            if (
                final in {IngestionJobStatus.SUCCEEDED, IngestionJobStatus.PARTIAL}
                and any(
                    status == DocumentProcessingStatus.SUCCEEDED for status in statuses.values()
                )
                and existing_index_job is None
            ):
                session.add(
                    KnowledgeIndexJob(
                        ingestion_job_id=claim.job_id,
                        status=KnowledgeIndexJobStatus.PENDING,
                        collection_alias=self.index_collection_alias,
                        collection_name=self.index_collection_name,
                        embedding_model=self.index_embedding_model,
                        chunking_version=self.index_chunking_version,
                    )
                )
            existing_extraction_job = await session.scalar(
                select(StructuredExtractionJob).where(
                    StructuredExtractionJob.ingestion_session_id == job.session_id
                )
            )
            if (
                self.structured_extraction_enabled
                and final in {IngestionJobStatus.SUCCEEDED, IngestionJobStatus.PARTIAL}
                and existing_extraction_job is None
            ):
                session.add(
                    StructuredExtractionJob(
                        ingestion_session_id=job.session_id,
                        requested_by_user_id=user_id,
                        status=ExtractionJobStatus.PENDING,
                    )
                )

    async def _record_worker_failure(self, claim: JobClaim, error: Exception) -> None:
        async with self.session_factory.begin() as session:
            job = await session.get(IngestionJob, claim.job_id, with_for_update=True)
            if job is None:
                return
            job.last_error = _bounded_error(error)
            job.locked_at = None
            job.lease_expires_at = None
            job.worker_id = None
            if claim.attempt >= self.max_attempts:
                job.status = IngestionJobStatus.FAILED
                job.finished_at = datetime.now(UTC)
            else:
                job.status = IngestionJobStatus.PENDING
                job.available_at = datetime.now(UTC) + timedelta(seconds=claim.attempt * 2)

    @staticmethod
    async def _telegram_id(session: AsyncSession, user_id: int) -> int:
        user = await session.get(User, user_id)
        if user is None:
            raise RuntimeError("Job owner is missing")
        return user.telegram_user_id


def worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"


def _bounded_error(error: Exception) -> str:
    return str(error).replace("\n", " ")[:2000] or error.__class__.__name__
