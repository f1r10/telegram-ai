import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    DocumentParseRun,
    DocumentProcessingStatus,
    FileObject,
    FileObjectState,
    IngestionJob,
    IngestionJobStatus,
    IngestionSession,
    KnowledgeIndexJob,
    KnowledgeIndexJobStatus,
    KnowledgeIndexRecord,
    KnowledgeIndexRecordStatus,
    OutboxEvent,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.knowledge.indexing import KnowledgeIndexService
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.knowledge.qdrant_store import QdrantKnowledgeStore

pytestmark = pytest.mark.integration


class _FakeDense:
    model_id = "test-dense"
    dimension = 3

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class _FakeSparse:
    model_id = "test-bm25"

    def embed(self, texts: list[str]) -> list[tuple[list[int], list[float]]]:
        return [([1, 2], [1.0, 0.5]) for _ in texts]


def test_index_job_is_durable_idempotent_and_searchable() -> None:
    settings = load_settings()
    if not settings.postgres_password or not settings.qdrant_url:
        pytest.skip("PostgreSQL and Qdrant integration settings are not configured")
    root = Path(settings.file_storage_path) / "phase5-index-test"
    storage = LocalFileStorage(root)
    store = QdrantKnowledgeStore(
        settings.qdrant_url,
        alias="phase5_index_test",
        collection="phase5_index_test_v1",
    )

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        job_id = 0
        try:
            async with factory.begin() as session:
                await session.execute(delete(KnowledgeIndexJob))
                await session.execute(delete(DocumentParseRun))
                await session.execute(delete(IngestionJob))
                await session.execute(delete(IngestionSession))
                await session.execute(delete(FileObject))
                await session.execute(delete(User))
                user = User(telegram_user_id=950001, role=UserRole.USER, status=UserStatus.ACTIVE)
                session.add(user)
                await session.flush()
                ingestion_session = IngestionSession(
                    user_id=user.id,
                    status="COMMITTED",
                    expires_at=datetime.now(UTC),
                )
                session.add(ingestion_session)
                await session.flush()
                raw_key = "blobs/phase5-index.txt"
                raw_path = storage.path_for_key(raw_key)
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_text("raw source remains unchanged", encoding="utf-8")
                file_object = FileObject(
                    storage_key=raw_key,
                    original_filename="phase5-index.txt",
                    content_type="text/plain",
                    byte_size=raw_path.stat().st_size,
                    sha256="b" * 64,
                    state=FileObjectState.ACTIVE,
                )
                session.add(file_object)
                await session.flush()
                ingestion_job = IngestionJob(
                    session_id=ingestion_session.id,
                    status=IngestionJobStatus.SUCCEEDED,
                    attempt_count=1,
                )
                session.add(ingestion_job)
                await session.flush()
                parse_run = DocumentParseRun(
                    file_object_id=file_object.id,
                    ingestion_job_id=ingestion_job.id,
                    status=DocumentProcessingStatus.SUCCEEDED,
                    parser_name="txt",
                    parser_version="test",
                )
                session.add(parse_run)
                await session.flush()
                normalized_key = storage.write_derived_json(
                    file_object.id,
                    parse_run.id,
                    {
                        "schema_version": "v1",
                        "blocks": [
                            {
                                "block_type": "paragraph",
                                "text": "KA45 zəmanət müddəti 24 aydır.",
                                "locator": {"line_start": 1, "line_end": 1},
                            }
                        ],
                    },
                )
                parse_run.normalized_storage_key = normalized_key
                index_job = KnowledgeIndexJob(
                    ingestion_job_id=ingestion_job.id,
                    status=KnowledgeIndexJobStatus.PENDING,
                    collection_alias="phase5_index_test",
                    collection_name="phase5_index_test_v1",
                    embedding_model="test-dense",
                    chunking_version="v1-medium-1800-180",
                )
                session.add(index_job)
                await session.flush()
                job_id = index_job.id

            service = KnowledgeIndexService(
                factory,
                storage,
                store,
                _FakeDense(),  # type: ignore[arg-type]
                _FakeSparse(),  # type: ignore[arg-type]
                chunking=ChunkingConfig("v1-medium-1800-180", 1800, 180),
            )
            claim = await service.claim_next("phase5-test")
            assert claim is not None
            await service.process(claim)
            async with factory() as session:
                row = await session.get(KnowledgeIndexJob, job_id)
                record = (await session.execute(select(KnowledgeIndexRecord))).scalar_one()
                assert row is not None and row.status == KnowledgeIndexJobStatus.SUCCEEDED
                assert record.status == KnowledgeIndexRecordStatus.SUCCEEDED
                assert record.chunk_count == 1
                indexed_notice = await session.scalar(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_type == "knowledge_index_job",
                        OutboxEvent.aggregate_id == str(job_id),
                        OutboxEvent.event_type == "KNOWLEDGE_INDEX_COMPLETED",
                    )
                )
                assert indexed_notice is not None
                assert indexed_notice.recipient_telegram_id == 950001
                assert indexed_notice.payload["files_indexed"] == 1
                assert indexed_notice.payload["chunks_indexed"] == 1
            found = await store.search_hybrid([1.0, 0.0, 0.0], ([1, 2], [1.0, 0.5]), limit=1)
            assert found[0].source_file_id > 0
            assert raw_path.read_text(encoding="utf-8") == "raw source remains unchanged"
            assert await service.claim_next("phase5-test") is None
        finally:
            await store.client.delete_collection(store.collection)
            await store.close()
            await engine.dispose()
            shutil.rmtree(root, ignore_errors=True)

    asyncio.run(scenario())
