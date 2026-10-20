"""Legacy reindex refuses changed, superseded and replay-owned PDF sources.

Uses disposable PostgreSQL; no real OCR, Qdrant vectors or company files.
"""

import asyncio
import shutil
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    DocumentParseRun,
    DocumentProcessingStatus,
    FileObject,
    FileObjectState,
    IngestionItemType,
    IngestionJob,
    IngestionJobStatus,
    IngestionSession,
    IngestionSessionItem,
    IngestionSessionStatus,
    KnowledgeIndexJob,
    KnowledgeIndexJobStatus,
    KnowledgeIndexRecord,
    KnowledgeIndexRecordStatus,
    OcrReplayJob,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.scripts.repair_legacy_index import repair

pytestmark = pytest.mark.integration


def test_legacy_reindex_revalidates_source_and_preserves_good_sibling() -> None:
    base = load_settings()
    if not (base.postgres_host and base.postgres_db and base.postgres_password):
        pytest.skip("Disposable PostgreSQL integration settings required")
    root = Path(base.file_storage_path) / "v2-legacy-provenance-repair-test"
    settings = base.model_copy(update={"file_storage_path": str(root)})
    storage = LocalFileStorage(root)
    raw_payload = b"%PDF-1.7\nsynthetic, private-data-free legacy repair fixture\n"

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        ids: dict[str, int] = {}
        try:
            async with factory.begin() as db:
                user = User(
                    telegram_user_id=939933,
                    role=UserRole.USER,
                    status=UserStatus.ACTIVE,
                )
                db.add(user)
                await db.flush()
                ids["user"] = user.id
                session = IngestionSession(
                    user_id=user.id,
                    status=IngestionSessionStatus.COMMITTED,
                    expires_at=datetime.now(UTC),
                )
                db.add(session)
                await db.flush()
                ids["session"] = session.id
                ingestion = IngestionJob(
                    session_id=session.id,
                    status=IngestionJobStatus.SUCCEEDED,
                    attempt_count=1,
                )
                db.add(ingestion)
                await db.flush()
                ids["ingestion"] = ingestion.id
                for index, (name, payload, mime) in enumerate(
                    (
                        ("legacy-scanned.pdf", raw_payload, "application/pdf"),
                        ("sibling-good.txt", b"healthy sibling source", "text/plain"),
                    ),
                    start=1,
                ):
                    disk_name = f"repair-{index}-{name}"
                    (storage.blob_root / disk_name).write_bytes(payload)
                    file = FileObject(
                        storage_key=f"blobs/{disk_name}",
                        original_filename=name,
                        sha256=sha256(payload).hexdigest(),
                        content_type=mime,
                        byte_size=len(payload),
                        state=FileObjectState.ACTIVE,
                    )
                    db.add(file)
                    await db.flush()
                    ids[f"file{index}"] = file.id
                    item = IngestionSessionItem(
                        session_id=session.id,
                        item_type=IngestionItemType.FILE,
                        file_object_id=file.id,
                        submitted_filename=name,
                        sender_user_id=user.id,
                        telegram_chat_id=939933,
                        telegram_message_id=1600 + index,
                        sequence_no=index,
                    )
                    db.add(item)
                    await db.flush()
                    run = DocumentParseRun(
                        file_object_id=file.id,
                        ingestion_job_id=ingestion.id,
                        session_item_id=item.id,
                        status=(
                            DocumentProcessingStatus.DEFERRED
                            if index == 1
                            else DocumentProcessingStatus.SUCCEEDED
                        ),
                        parser_name="pypdf" if index == 1 else "txt",
                        parser_version="fixture",
                        requires_ocr=index == 1,
                    )
                    db.add(run)
                    await db.flush()
                    ids[f"run{index}"] = run.id
                    run.normalized_storage_key = storage.write_derived_json(
                        file.id,
                        run.id,
                        {
                            "schema_version": "v1",
                            "blocks": [
                                {
                                    "sequence": 1,
                                    "block_type": "page_text",
                                    "text": (
                                        "KA45 verified partial PDF text for search"
                                        if index == 1
                                        else "Healthy independent sibling content"
                                    ),
                                    "locator": {"page": 1},
                                }
                            ],
                        },
                    )
                job = KnowledgeIndexJob(
                    ingestion_job_id=ingestion.id,
                    status=KnowledgeIndexJobStatus.SUCCEEDED,
                    collection_alias=settings.qdrant_collection_alias,
                    collection_name=settings.qdrant_collection_name,
                    embedding_model=settings.embedding_model,
                    chunking_version=settings.chunking_version,
                )
                db.add(job)
                await db.flush()
                ids["index"] = job.id
                for n, chunk_count in ((1, 0), (2, 1)):
                    db.add(
                        KnowledgeIndexRecord(
                            knowledge_index_job_id=job.id,
                            parse_run_id=ids[f"run{n}"],
                            status=KnowledgeIndexRecordStatus.SUCCEEDED,
                            collection_name=job.collection_name,
                            embedding_model=job.embedding_model,
                            chunking_version=job.chunking_version,
                            chunk_count=chunk_count,
                        )
                    )

            source_path = storage.blob_root / "repair-1-legacy-scanned.pdf"
            source_path.write_bytes(b"%PDF-1.7\nTAMPERED\n")
            bad_source = await repair(settings, apply=True, limit=500)
            assert bad_source.applied == 0
            async with factory() as db:
                run = await db.get(DocumentParseRun, ids["run1"])
                assert run is not None and run.status == DocumentProcessingStatus.DEFERRED
            source_path.write_bytes(raw_payload)

            async with factory.begin() as db:
                newer = DocumentParseRun(
                    file_object_id=ids["file1"],
                    ingestion_job_id=ids["ingestion"],
                    status=DocumentProcessingStatus.FAILED,
                    parser_name="pypdf",
                    parser_version="newer",
                )
                db.add(newer)
                await db.flush()
                ids["newer"] = newer.id
            assert (await repair(settings, apply=True, limit=500)).applied == 0
            async with factory.begin() as db:
                newer = await db.get(DocumentParseRun, ids["newer"])
                assert newer is not None
                await db.delete(newer)

            async with factory.begin() as db:
                db.add(
                    OcrReplayJob(
                        file_object_id=ids["file1"],
                        source_parse_run_id=ids["run1"],
                        status="PENDING",
                        attempt_count=0,
                    )
                )
            assert (await repair(settings, apply=True, limit=500)).applied == 0
            async with factory.begin() as db:
                replay = await db.scalar(
                    select(OcrReplayJob).where(OcrReplayJob.file_object_id == ids["file1"])
                )
                assert replay is not None
                await db.delete(replay)

            plan = await repair(settings, apply=False, limit=500)
            assert plan.eligible >= 1
            assert plan.applied == 0
            applied = await repair(settings, apply=True, limit=500)
            assert applied.applied == 1
            again = await repair(settings, apply=True, limit=500)
            assert again.applied == 0

            async with factory() as db:
                source = await db.get(DocumentParseRun, ids["run1"])
                assert source is not None
                assert source.status == DocumentProcessingStatus.SUCCEEDED
                index = await db.get(KnowledgeIndexJob, ids["index"])
                assert index is not None and index.status == KnowledgeIndexJobStatus.PENDING
                sibling = await db.scalar(
                    select(KnowledgeIndexRecord).where(
                        KnowledgeIndexRecord.parse_run_id == ids["run2"]
                    )
                )
                target = await db.scalar(
                    select(KnowledgeIndexRecord).where(
                        KnowledgeIndexRecord.parse_run_id == ids["run1"]
                    )
                )
                assert sibling is not None and target is not None
                assert sibling.status == KnowledgeIndexRecordStatus.SUCCEEDED
                assert sibling.chunk_count == 1
                assert target.status == KnowledgeIndexRecordStatus.FAILED
                events = (
                    await db.scalars(
                        select(AuditEvent).where(
                            AuditEvent.entity_type == "document_parse_run",
                            AuditEvent.entity_id == str(ids["run1"]),
                            AuditEvent.action == "LEGACY_REINDEX_OPERATOR_QUEUED",
                        )
                    )
                ).all()
                assert len(events) == 1
        finally:
            async with factory.begin() as db:
                await db.execute(
                    delete(AuditEvent).where(
                        AuditEvent.entity_type == "document_parse_run",
                        AuditEvent.entity_id == str(ids.get("run1", -1)),
                    )
                )
                await db.execute(
                    delete(KnowledgeIndexRecord).where(
                        KnowledgeIndexRecord.knowledge_index_job_id == ids.get("index", -1)
                    )
                )
                await db.execute(
                    delete(KnowledgeIndexJob).where(KnowledgeIndexJob.id == ids.get("index", -1))
                )
                await db.execute(
                    delete(DocumentParseRun).where(
                        DocumentParseRun.ingestion_job_id == ids.get("ingestion", -1)
                    )
                )
                await db.execute(
                    delete(IngestionSessionItem).where(
                        IngestionSessionItem.session_id == ids.get("session", -1)
                    )
                )
                await db.execute(
                    delete(IngestionJob).where(IngestionJob.id == ids.get("ingestion", -1))
                )
                await db.execute(
                    delete(FileObject).where(
                        FileObject.id.in_([ids.get("file1", -1), ids.get("file2", -1)])
                    )
                )
                await db.execute(
                    delete(IngestionSession).where(IngestionSession.id == ids.get("session", -1))
                )
                await db.execute(delete(User).where(User.id == ids.get("user", -1)))
            await engine.dispose()
            shutil.rmtree(root, ignore_errors=True)

    asyncio.run(scenario())
