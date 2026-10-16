"""Durable indexing of normalized parse runs into Qdrant."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.infrastructure.database.models import (
    DocumentParseRun,
    DocumentProcessingStatus,
    FileObject,
    KnowledgeIndexJob,
    KnowledgeIndexJobStatus,
    KnowledgeIndexRecord,
    KnowledgeIndexRecordStatus,
)
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.knowledge.chunking import chunk_normalized_artifact
from office_assistant.knowledge.embeddings import FastEmbedSparseAdapter, FastEmbedTextAdapter
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.knowledge.qdrant_store import QdrantKnowledgeStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IndexJobClaim:
    job_id: int
    attempt: int


class KnowledgeIndexService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        store: QdrantKnowledgeStore,
        embedder: FastEmbedTextAdapter,
        sparse_embedder: FastEmbedSparseAdapter,
        *,
        chunking: ChunkingConfig,
        lease_seconds: int = 300,
        max_attempts: int = 3,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.store = store
        self.embedder = embedder
        self.sparse_embedder = sparse_embedder
        self.chunking = chunking
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    async def claim_next(self, worker_id: str) -> IndexJobClaim | None:
        now = datetime.now(UTC)
        async with self.session_factory.begin() as session:
            while True:
                statement = (
                    select(KnowledgeIndexJob)
                    .where(
                        (
                            (KnowledgeIndexJob.status == KnowledgeIndexJobStatus.PENDING)
                            & (KnowledgeIndexJob.available_at <= now)
                        )
                        | (
                            (KnowledgeIndexJob.status == KnowledgeIndexJobStatus.RUNNING)
                            & (KnowledgeIndexJob.lease_expires_at <= now)
                        )
                    )
                    .order_by(KnowledgeIndexJob.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                job = (await session.execute(statement)).scalar_one_or_none()
                if job is None:
                    return None
                if job.attempt_count >= self.max_attempts:
                    job.status = KnowledgeIndexJobStatus.FAILED
                    job.finished_at = now
                    job.last_error = "Maximum index attempts exceeded"
                    job.locked_at = None
                    job.lease_expires_at = None
                    job.worker_id = None
                    continue
                job.attempt_count += 1
                job.status = KnowledgeIndexJobStatus.RUNNING
                job.started_at = job.started_at or now
                job.locked_at = now
                job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                job.worker_id = worker_id
                job.last_error = None
                return IndexJobClaim(job.id, job.attempt_count)

    async def process(self, claim: IndexJobClaim) -> None:
        try:
            await self._process_job(claim)
        except Exception as error:
            await self._record_failure(claim, error)
            logger.exception("Knowledge index job failed | job_id=%s", claim.job_id)

    async def _process_job(self, claim: IndexJobClaim) -> None:
        async with self.session_factory() as session:
            job = await session.get(KnowledgeIndexJob, claim.job_id)
            if job is None or job.status != KnowledgeIndexJobStatus.RUNNING:
                return
            parse_runs = list(
                (
                    await session.execute(
                        select(DocumentParseRun, FileObject)
                        .join(FileObject, FileObject.id == DocumentParseRun.file_object_id)
                        .where(
                            DocumentParseRun.ingestion_job_id == job.ingestion_job_id,
                            DocumentParseRun.status == DocumentProcessingStatus.SUCCEEDED,
                        )
                    )
                ).all()
            )
        statuses: list[str] = []
        for parse_run, file_object in parse_runs:
            statuses.append(await self._process_parse_run(claim, job, parse_run, file_object))
        success = {KnowledgeIndexRecordStatus.SUCCEEDED}
        # A job that indexed no documents cannot truthfully be SUCCEEDED.
        final = (
            KnowledgeIndexJobStatus.SUCCEEDED
            if statuses and all(status in success for status in statuses)
            else KnowledgeIndexJobStatus.PARTIAL
            if any(status in success for status in statuses)
            else KnowledgeIndexJobStatus.FAILED
        )
        async with self.session_factory.begin() as session:
            row = await session.get(KnowledgeIndexJob, claim.job_id, with_for_update=True)
            if row is not None:
                row.status = final
                row.last_error = (
                    "No searchable parsed files were available for indexing"
                    if not statuses
                    else None
                )
                row.finished_at = datetime.now(UTC)
                row.locked_at = None
                row.lease_expires_at = None
                row.worker_id = None

    async def _process_parse_run(
        self,
        claim: IndexJobClaim,
        job: KnowledgeIndexJob,
        parse_run: DocumentParseRun,
        file_object: FileObject,
    ) -> str:
        async with self.session_factory.begin() as session:
            record = await session.scalar(
                select(KnowledgeIndexRecord).where(
                    KnowledgeIndexRecord.knowledge_index_job_id == claim.job_id,
                    KnowledgeIndexRecord.parse_run_id == parse_run.id,
                )
            )
            if record is not None and record.status == KnowledgeIndexRecordStatus.SUCCEEDED:
                return KnowledgeIndexRecordStatus.SUCCEEDED
            if record is None:
                record = KnowledgeIndexRecord(
                    knowledge_index_job_id=claim.job_id,
                    parse_run_id=parse_run.id,
                    status=KnowledgeIndexRecordStatus.RUNNING,
                    collection_name=job.collection_name,
                    embedding_model=job.embedding_model,
                    chunking_version=job.chunking_version,
                )
                session.add(record)
                await session.flush()
            else:
                record.status = KnowledgeIndexRecordStatus.RUNNING
                record.last_error = None
            artifact_key = parse_run.normalized_storage_key
            record_id = record.id
        if not artifact_key:
            return await self._finish_record(
                record_id,
                KnowledgeIndexRecordStatus.FAILED,
                0,
                "Normalized document artifact is missing; no searchable text indexed",
            )
        try:
            chunks = chunk_normalized_artifact(
                self.storage.path_for_key(artifact_key),
                source_file_id=file_object.id,
                parse_run_id=parse_run.id,
                config=self.chunking,
            )
            if not chunks:
                return await self._finish_record(
                    record_id,
                    KnowledgeIndexRecordStatus.FAILED,
                    0,
                    "Document contains no indexable text; OCR or manual review is required",
                )
            await self.store.ensure_collection(self.embedder.dimension)
            dense = await asyncio.to_thread(
                self.embedder.embed_documents, [chunk.text for chunk in chunks]
            )
            sparse = await asyncio.to_thread(
                self.sparse_embedder.embed, [chunk.text for chunk in chunks]
            )
            await self.store.upsert_chunks(
                chunks,
                dense,
                sparse,
                source_filename=file_object.original_filename,
                media_type=file_object.content_type,
                embedding_model=self.embedder.model_id,
            )
        except Exception as error:
            return await self._finish_record(
                record_id, KnowledgeIndexRecordStatus.FAILED, 0, str(error)[:2000]
            )
        return await self._finish_record(
            record_id, KnowledgeIndexRecordStatus.SUCCEEDED, len(chunks)
        )

    async def _finish_record(
        self, record_id: int, status: str, chunk_count: int, error: str | None = None
    ) -> str:
        async with self.session_factory.begin() as session:
            record = await session.get(KnowledgeIndexRecord, record_id, with_for_update=True)
            if record is not None:
                record.status = status
                record.chunk_count = chunk_count
                record.last_error = error
                record.indexed_at = (
                    datetime.now(UTC) if status == KnowledgeIndexRecordStatus.SUCCEEDED else None
                )
        return status

    async def _record_failure(self, claim: IndexJobClaim, error: Exception) -> None:
        async with self.session_factory.begin() as session:
            job = await session.get(KnowledgeIndexJob, claim.job_id, with_for_update=True)
            if job is None:
                return
            job.last_error = str(error).replace("\n", " ")[:2000]
            job.locked_at = None
            job.lease_expires_at = None
            job.worker_id = None
            if claim.attempt >= self.max_attempts:
                job.status = KnowledgeIndexJobStatus.FAILED
                job.finished_at = datetime.now(UTC)
            else:
                job.status = KnowledgeIndexJobStatus.PENDING
                job.available_at = datetime.now(UTC) + timedelta(seconds=claim.attempt * 2)
