"""Durable scanned-PDF replay against disposable PostgreSQL (no paid OCR)."""

import asyncio
import shutil
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

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
from office_assistant.processing.models import ContentBlock, ParsedDocument
from office_assistant.processing.ocr_replay import OcrReplayService
from office_assistant.processing.parsers import ParserRegistry
from office_assistant.scripts.replay_scanned_pdfs import queue_legacy_scans
from office_assistant.scripts.retry_scanned_pdfs import retry_failed

pytestmark = pytest.mark.integration


class _FakeOcrParser:
    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument:
        assert path.read_bytes().startswith(b"%PDF-")
        return ParsedDocument(
            source_file_id,
            "pypdf",
            "fixture",
            media_type,
            {},
            [ContentBlock(1, "page_ocr", "KA45 recovered original scan", {"page": 1})],
        )


class _FakeRegistry:
    def for_filename(self, _filename: str) -> _FakeOcrParser:
        return _FakeOcrParser()


class _FakeBlankParser:
    def parse(self, _path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument:
        return ParsedDocument(source_file_id, "pypdf", "fixture", media_type, {}, [])


class _FakeBlankRegistry:
    def for_filename(self, _filename: str) -> _FakeBlankParser:
        return _FakeBlankParser()


def test_replay_is_idempotent_fenced_and_preserves_other_session_documents() -> None:
    base = load_settings()
    if not (base.postgres_db and base.postgres_host and base.postgres_password):
        pytest.skip("PostgreSQL is required")
    root = Path(base.file_storage_path) / "v2-ocr-replay-integration"
    settings = base.model_copy(update={"file_storage_path": str(root)})
    storage = LocalFileStorage(root)

    async def run() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        ids: dict[str, int] = {}
        try:
            async with factory.begin() as db:
                owner = User(
                    telegram_user_id=939901,
                    role=UserRole.USER,
                    status=UserStatus.ACTIVE,
                )
                db.add(owner)
                await db.flush()
                ids["user"] = owner.id
                submission = IngestionSession(
                    user_id=owner.id,
                    status=IngestionSessionStatus.COMMITTED,
                    expires_at=datetime.now(UTC),
                )
                db.add(submission)
                await db.flush()
                ids["session"] = submission.id
                job = IngestionJob(
                    session_id=submission.id,
                    status=IngestionJobStatus.SUCCEEDED,
                    attempt_count=1,
                )
                db.add(job)
                await db.flush()
                ids["job"] = job.id
                files: list[FileObject] = []
                for i, (filename, mime, payload) in enumerate(
                    [
                        ("old-scan.pdf", "application/pdf", b"%PDF-old-scanned-sample"),
                        ("known-good.txt", "text/plain", b"already indexed"),
                    ],
                    start=1,
                ):
                    filename_on_disk = f"{i}-{filename}"
                    path = storage.blob_root / filename_on_disk
                    path.write_bytes(payload)
                    record = FileObject(
                        storage_key=f"blobs/{filename_on_disk}",
                        original_filename=filename,
                        content_type=mime,
                        byte_size=len(payload),
                        sha256=sha256(payload).hexdigest(),
                        state=FileObjectState.ACTIVE,
                    )
                    db.add(record)
                    await db.flush()
                    files.append(record)
                    ids[f"file{i}"] = record.id
                    item = IngestionSessionItem(
                        session_id=submission.id,
                        item_type=IngestionItemType.FILE,
                        file_object_id=record.id,
                        submitted_filename=filename,
                        sender_user_id=owner.id,
                        telegram_chat_id=939901,
                        telegram_message_id=i + 1000,
                        sequence_no=i,
                    )
                    db.add(item)
                    await db.flush()
                    run = DocumentParseRun(
                        file_object_id=record.id,
                        ingestion_job_id=job.id,
                        session_item_id=item.id,
                        status=(
                            DocumentProcessingStatus.DEFERRED
                            if i == 1
                            else DocumentProcessingStatus.SUCCEEDED
                        ),
                        parser_name="pypdf" if i == 1 else "txt",
                        parser_version="historic",
                        requires_ocr=i == 1,
                    )
                    db.add(run)
                    await db.flush()
                    ids[f"run{i}"] = run.id
                    payload_json: dict[str, object] = {
                        "blocks": (
                            []
                            if i == 1
                            else [
                                {
                                    "sequence": 1,
                                    "text": "already indexed",
                                    "block_type": "plain_text",
                                    "locator": {"line_start": 1},
                                }
                            ]
                        )
                    }
                    run.normalized_storage_key = storage.write_derived_json(
                        record.id, run.id, payload_json
                    )
                index_job = KnowledgeIndexJob(
                    ingestion_job_id=job.id,
                    status=KnowledgeIndexJobStatus.SUCCEEDED,
                    collection_alias="document_chunks",
                    collection_name="document_chunks_v1",
                    embedding_model=settings.embedding_model,
                    chunking_version=settings.chunking_version,
                )
                db.add(index_job)
                await db.flush()
                ids["index"] = index_job.id
                db.add(
                    KnowledgeIndexRecord(
                        knowledge_index_job_id=index_job.id,
                        parse_run_id=ids["run2"],
                        status=KnowledgeIndexRecordStatus.SUCCEEDED,
                        collection_name=index_job.collection_name,
                        embedding_model=index_job.embedding_model,
                        chunking_version=index_job.chunking_version,
                        chunk_count=1,
                    )
                )

            preview = await queue_legacy_scans(settings, apply=False, limit=20)
            assert (preview.inspected, preview.eligible, preview.queued) == (1, 1, 0)
            queued = await queue_legacy_scans(settings, apply=True, limit=20)
            assert (queued.inspected, queued.eligible, queued.queued) == (1, 1, 1)
            repeated = await queue_legacy_scans(settings, apply=True, limit=20)
            assert repeated.queued == 0

            empty = OcrReplayService(
                factory, storage, settings, registry=cast(ParserRegistry, _FakeBlankRegistry())
            )
            blank_claim = await empty.claim_next("blank-worker")
            assert blank_claim is not None
            await empty.process(blank_claim)
            assert (await retry_failed(settings, kind="ocr", limit=20)).eligible == 1
            assert (await retry_failed(settings, kind="index", limit=20)).eligible == 0
            assert (await retry_failed(settings, kind="ocr", apply=True, limit=20)).queued == 1
            assert (await retry_failed(settings, kind="ocr", apply=True, limit=20)).queued == 0

            replay = OcrReplayService(
                factory, storage, settings, registry=cast(ParserRegistry, _FakeRegistry())
            )
            first, second = await asyncio.gather(replay.claim_next("w1"), replay.claim_next("w2"))
            claims = [claim for claim in [first, second] if claim is not None]
            assert len(claims) == 1
            # Old worker's attempt cannot commit after operator retry advanced the lease.
            await empty._commit(
                blank_claim,
                _FakeOcrParser().parse(
                    storage.blob_root / "1-old-scan.pdf",
                    source_file_id=ids["file1"],
                    media_type="application/pdf",
                ),
            )
            await replay.process(claims[0])
            async with factory() as db:
                row = (await db.execute(select(OcrReplayJob))).scalar_one()
                assert row.status == "INDEX_QUEUED"
                assert row.file_object_id == ids["file1"]
                assert row.source_parse_run_id == ids["run1"]
                assert row.result_parse_run_id is not None
                original = await db.get(DocumentParseRun, ids["run1"])
                current = await db.get(DocumentParseRun, row.result_parse_run_id)
                assert original is not None and current is not None
                assert original.status == DocumentProcessingStatus.DEFERRED
                assert current.status == DocumentProcessingStatus.SUCCEEDED
                assert current.file_object_id == original.file_object_id
                assert current.session_item_id == original.session_item_id
                assert current.ingestion_job_id == original.ingestion_job_id
                assert storage.exists(current.normalized_storage_key or "")
                assert "KA45" in storage.path_for_key(current.text_storage_key or "").read_text()
                idx = await db.get(KnowledgeIndexJob, ids["index"])
                assert idx is not None and idx.status == KnowledgeIndexJobStatus.PENDING
                good = (
                    await db.execute(
                        select(KnowledgeIndexRecord).where(
                            KnowledgeIndexRecord.parse_run_id == ids["run2"]
                        )
                    )
                ).scalar_one()
                assert good.status == KnowledgeIndexRecordStatus.SUCCEEDED
                assert good.chunk_count == 1
                items = (
                    (
                        await db.execute(
                            select(IngestionSessionItem).where(
                                IngestionSessionItem.session_id == ids["session"]
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(items) == 2
                assert sorted(item.telegram_message_id for item in items) == [1001, 1002]
            # The OCR parse is immutable. A failed target record is retried
            # without rerunning OCR or modifying a good sibling index record.
            async with factory.begin() as db:
                replay_row = (await db.execute(select(OcrReplayJob))).scalar_one()
                ids["replay"] = replay_row.id
                assert replay_row.result_parse_run_id is not None
                ids["recovered"] = replay_row.result_parse_run_id
                index_row = await db.get(KnowledgeIndexJob, ids["index"])
                assert index_row is not None
                index_row.status = KnowledgeIndexJobStatus.FAILED
                db.add(
                    KnowledgeIndexRecord(
                        knowledge_index_job_id=ids["index"],
                        parse_run_id=ids["recovered"],
                        status=KnowledgeIndexRecordStatus.FAILED,
                        collection_name=index_row.collection_name,
                        embedding_model=index_row.embedding_model,
                        chunking_version=index_row.chunking_version,
                        chunk_count=0,
                    )
                )
            assert (await retry_failed(settings, kind="index", limit=20)).eligible == 1
            r1, r2 = await asyncio.gather(
                retry_failed(settings, kind="index", apply=True, limit=20),
                retry_failed(settings, kind="index", apply=True, limit=20),
            )
            assert r1.queued + r2.queued == 1
            assert (await retry_failed(settings, kind="index", apply=True, limit=20)).queued == 0
            assert (await retry_failed(settings, kind="ocr", apply=True, limit=20)).queued == 0
            async with factory() as db:
                index_row = await db.get(KnowledgeIndexJob, ids["index"])
                assert index_row is not None
                assert index_row.status == KnowledgeIndexJobStatus.PENDING
                assert index_row.attempt_count == 0
                good = await db.scalar(
                    select(KnowledgeIndexRecord).where(
                        KnowledgeIndexRecord.parse_run_id == ids["run2"]
                    )
                )
                assert good is not None
                assert good.status == KnowledgeIndexRecordStatus.SUCCEEDED
                assert good.chunk_count == 1
                target = await db.scalar(
                    select(KnowledgeIndexRecord).where(
                        KnowledgeIndexRecord.parse_run_id == ids["recovered"]
                    )
                )
                assert target is not None
                assert target.status == KnowledgeIndexRecordStatus.FAILED
                parses = list(
                    (
                        await db.scalars(
                            select(DocumentParseRun).where(
                                DocumentParseRun.ingestion_job_id == ids["job"]
                            )
                        )
                    ).all()
                )
                assert len(parses) == 3
                events = list(
                    (
                        await db.scalars(
                            select(AuditEvent).where(
                                AuditEvent.entity_type == "ocr_replay_job",
                                AuditEvent.entity_id == str(ids["replay"]),
                            )
                        )
                    ).all()
                )
                assert [
                    event.event_metadata["phase"] for event in events if event.event_metadata
                ] == ["ocr", "index"]
            # Replaced original bytes cannot be silently used to retry a failed projection.
            raw = storage.blob_root / "1-old-scan.pdf"
            original_bytes = raw.read_bytes()
            raw.write_bytes(b"%PDF-modified-content")
            async with factory.begin() as db:
                index_row = await db.get(KnowledgeIndexJob, ids["index"])
                assert index_row is not None
                index_row.status = KnowledgeIndexJobStatus.FAILED
            assert (await retry_failed(settings, kind="index", apply=True, limit=20)).queued == 0
            raw.write_bytes(original_bytes)

        finally:
            if ids.get("job"):
                async with factory.begin() as db:
                    if "replay" in ids:
                        await db.execute(
                            delete(AuditEvent).where(
                                AuditEvent.entity_type == "ocr_replay_job",
                                AuditEvent.entity_id == str(ids["replay"]),
                            )
                        )
                    await db.execute(
                        delete(OcrReplayJob).where(OcrReplayJob.file_object_id == ids["file1"])
                    )
                    await db.execute(
                        delete(KnowledgeIndexRecord).where(
                            KnowledgeIndexRecord.knowledge_index_job_id == ids["index"]
                        )
                    )
                    await db.execute(
                        delete(KnowledgeIndexJob).where(KnowledgeIndexJob.id == ids["index"])
                    )
                    await db.execute(
                        delete(DocumentParseRun).where(
                            DocumentParseRun.ingestion_job_id == ids["job"]
                        )
                    )
                    await db.execute(delete(IngestionJob).where(IngestionJob.id == ids["job"]))
                    await db.execute(
                        delete(IngestionSessionItem).where(
                            IngestionSessionItem.session_id == ids["session"]
                        )
                    )
                    await db.execute(
                        delete(IngestionSession).where(IngestionSession.id == ids["session"])
                    )
                    await db.execute(
                        delete(FileObject).where(FileObject.id.in_([ids["file1"], ids["file2"]]))
                    )
                    await db.execute(delete(User).where(User.id == ids["user"]))
            await engine.dispose()
            shutil.rmtree(root, ignore_errors=True)

    asyncio.run(run())
