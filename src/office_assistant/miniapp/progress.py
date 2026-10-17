"""Authoritative, owner-scoped document processing progress for the Mini App.

Never infer completion from elapsed time, successful upload, or a successful parser:
search readiness requires a succeeded index job AND indexed searchable chunks.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.infrastructure.database.models import (
    DocumentParseRun,
    DocumentProcessingStatus,
    IngestionJob,
    IngestionJobStatus,
    IngestionSession,
    IngestionSessionItem,
    KnowledgeIndexJob,
    KnowledgeIndexJobStatus,
    KnowledgeIndexRecord,
    KnowledgeIndexRecordStatus,
    User,
)


class UploadJobNotFound(LookupError):
    """No such ingestion job belongs to this actor."""


def summarize_progress(
    *,
    ingest: str,
    parse_states: list[str],
    index: str | None,
    indexed_chunks: int,
) -> tuple[str, str, bool]:
    """Expose exactly what the durable DB proves, without invented percentages."""

    if ingest == IngestionJobStatus.FAILED:
        return "failed", "Sənədin oxunması alınmadı. Administratora bildirin.", True
    if ingest == IngestionJobStatus.PENDING:
        return "queued", "Fayl qəbul edilib, emal növbəsindədir.", False
    if ingest == IngestionJobStatus.RUNNING:
        return "reading", "Fayl oxunur; OCR tələb olunarsa bu mərhələdə aparılır.", False
    if ingest == IngestionJobStatus.PARTIAL:
        return "review", "Fayl qismən işlənib. Nəticə yoxlanmalıdır.", True
    if any(
        state in {DocumentProcessingStatus.FAILED, DocumentProcessingStatus.DEFERRED}
        for state in parse_states
    ):
        return "review", "Sənədin oxunmasında problem var; yoxlama tələb olunur.", True
    if not parse_states or not all(
        state == DocumentProcessingStatus.SUCCEEDED for state in parse_states
    ):
        return "reading", "Faylın tam oxunması hələ təsdiqlənməyib.", False
    if index == KnowledgeIndexJobStatus.FAILED:
        return "failed", "Axtarış indeksinin hazırlanması alınmadı.", True
    if index == KnowledgeIndexJobStatus.PARTIAL:
        return "review", "Mətnin yalnız bir hissəsi axtarışa daxil edilib.", True
    if index == KnowledgeIndexJobStatus.SUCCEEDED:
        if indexed_chunks > 0:
            return "ready", "Sənəd axtarış üçün hazırdır. «Bizim baza»da soruşun.", True
        return "review", "İndeks tamamlanıb, amma axtarıla bilən mətn təsdiqlənməyib.", True
    if index in {KnowledgeIndexJobStatus.PENDING, KnowledgeIndexJobStatus.RUNNING}:
        return "indexing", "Sənədin mətni axtarış indeksinə əlavə olunur.", False
    return "indexing", "Oxunma tamamlanıb; axtarış indeksi hələ hazır deyil.", False


class MiniAppDocumentProgress:
    """Read only: list last eight jobs and guard a single job by DB ownership."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def recent(self, actor: User, *, limit: int = 6) -> list[dict[str, object]]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(IngestionJob)
                    .join(IngestionSession, IngestionSession.id == IngestionJob.session_id)
                    .where(
                        IngestionSession.user_id == actor.id,
                        IngestionSession.status == "COMMITTED",
                    )
                    .order_by(IngestionJob.id.desc())
                    .limit(min(max(1, limit), 8))
                )
            ).scalars().all()
            return [await self._describe(session, row) for row in rows]

    async def one(self, actor: User, *, job_id: int) -> dict[str, object]:
        if type(job_id) is not int or not 1 <= job_id < 2**63:
            raise ValueError("Invalid ingestion job ID")
        async with self.session_factory() as session:
            job = await session.scalar(
                select(IngestionJob)
                .join(IngestionSession, IngestionSession.id == IngestionJob.session_id)
                .where(IngestionJob.id == job_id, IngestionSession.user_id == actor.id)
            )
            if job is None:
                raise UploadJobNotFound("Unknown document job for this user")
            return await self._describe(session, job)

    @staticmethod
    async def _describe(session: AsyncSession, job: IngestionJob) -> dict[str, object]:
        filename = await session.scalar(
            select(IngestionSessionItem.submitted_filename)
            .where(
                IngestionSessionItem.session_id == job.session_id,
                IngestionSessionItem.item_type == "FILE",
            )
            .order_by(IngestionSessionItem.sequence_no)
            .limit(1)
        )
        parse_states = list(
            (
                await session.execute(
                    select(DocumentParseRun.status)
                    .where(DocumentParseRun.ingestion_job_id == job.id)
                )
            )
            .scalars()
            .all()
        )
        index = await session.scalar(
            select(KnowledgeIndexJob).where(KnowledgeIndexJob.ingestion_job_id == job.id)
        )
        indexed_chunks = 0
        if index is not None and index.status == KnowledgeIndexJobStatus.SUCCEEDED:
            # Check every indexed record; a merely SUCCEEDED flag is not proof
            # that a document actually made it into the searchable collection.
            records = (
                await session.execute(
                    select(KnowledgeIndexRecord).where(
                        KnowledgeIndexRecord.knowledge_index_job_id == index.id
                    )
                )
            ).scalars().all()
            indexed_chunks = sum(
                row.chunk_count
                for row in records
                if row.status == KnowledgeIndexRecordStatus.SUCCEEDED
            )
        status, notice, terminal = summarize_progress(
            ingest=job.status,
            parse_states=parse_states,
            index=index.status if index is not None else None,
            indexed_chunks=indexed_chunks,
        )
        return {
            "job_id": job.id,
            "filename": (filename or "Sənəd")[:160],
            "stage": status,
            "notice": notice,
            "terminal": terminal,
        }
