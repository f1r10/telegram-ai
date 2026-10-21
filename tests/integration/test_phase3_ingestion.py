import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from shutil import rmtree

import pytest
from sqlalchemy import delete, select, update

from office_assistant.application.errors import AuthorizationDenied, InvalidStateTransition
from office_assistant.application.ingestion import IngestionSessionService, UploadMetadata
from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    DocumentParseRun,
    DocumentProcessingStatus,
    FileObject,
    FileObjectState,
    IngestionJob,
    IngestionJobStatus,
    IngestionSession,
    IngestionSessionItem,
    IngestionSessionStatus,
    KnowledgeIndexJob,
    KnowledgeIndexJobStatus,
    KnowledgeIndexRecord,
    KnowledgeIndexRecordStatus,
    SystemMetadata,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage, StorageError
from office_assistant.miniapp.progress import MiniAppDocumentProgress, UploadJobNotFound
from office_assistant.miniapp.uploads import MiniAppUploadWorkflow

pytestmark = pytest.mark.integration


def _settings_or_skip() -> Settings:
    settings = load_settings()
    if not all(
        (
            settings.postgres_db,
            settings.postgres_user,
            settings.postgres_password,
            settings.postgres_host,
        )
    ):
        pytest.skip("PostgreSQL integration settings are not configured")
    return settings


async def _reset(settings: Settings) -> User:
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory.begin() as session:
            # Parse rows are referenced by index records with RESTRICT;
            # remove the dependent projections before cascading ingestion jobs.
            await session.execute(delete(KnowledgeIndexRecord))
            await session.execute(delete(IngestionJob))
            await session.execute(delete(IngestionSessionItem))
            await session.execute(delete(IngestionSession))
            await session.execute(delete(FileObject))
            await session.execute(delete(AuditEvent))
            await session.execute(delete(User))
            await session.execute(
                update(SystemMetadata)
                .where(SystemMetadata.key == "bootstrap.admin.initialized")
                .values(value="false")
            )
            user = User(
                telegram_user_id=930001,
                telegram_username="uploader",
                first_name="Uploader",
                role=UserRole.USER,
                status=UserStatus.ACTIVE,
            )
            session.add(user)
            await session.flush()
            return user
    finally:
        await engine.dispose()


def _metadata(
    storage: LocalFileStorage, content: bytes, message_id: int, filename: str
) -> UploadMetadata:
    path, key = storage.create_staging_path(Path(filename).suffix)
    path.write_bytes(content)
    return UploadMetadata(
        original_filename=filename,
        content_type="application/pdf",
        staging_path=path,
        staging_key=key,
        telegram_chat_id=930001,
        telegram_message_id=message_id,
        telegram_update_id=message_id + 1000,
        caption="Oktyabr qiymətləri" if message_id == 1 else None,
    )


def test_ingestion_session_duplicate_commit_and_cancel() -> None:
    settings = _settings_or_skip()
    user = asyncio.run(_reset(settings))

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        storage = LocalFileStorage(Path(settings.file_storage_path) / "phase3-test")
        service = IngestionSessionService(factory, storage, max_files=10, ttl_hours=24)
        try:
            first = await service.add_file(user, _metadata(storage, b"same bytes", 1, "prices.pdf"))
            assert first.summary.file_count == 1
            assert not first.duplicate
            assert await service.add_instruction(
                user,
                text_content="Bu fayl ABC şirkətinə aiddir.",
                chat_id=930001,
                message_id=2,
                update_id=1002,
            )
            second = await service.add_file(
                user, _metadata(storage, b"same bytes", 3, "prices_copy.pdf")
            )
            assert second.duplicate

            committed = await service.commit(user)
            assert committed is not None
            assert committed.file_count == 2
            assert committed.instruction_count == 1
            assert committed.duplicate_count == 1
            assert committed.job_id is not None
            assert await service.commit(user) is None

            async with factory() as session:
                files = (await session.execute(select(FileObject))).scalars().all()
                jobs = (await session.execute(select(IngestionJob))).scalars().all()
                items = (
                    (
                        await session.execute(
                            select(IngestionSessionItem).order_by(IngestionSessionItem.sequence_no)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(files) == 1
                assert files[0].state == FileObjectState.ACTIVE
                assert len(jobs) == 1
                assert jobs[0].status == IngestionJobStatus.PENDING
                assert [item.item_type for item in items] == ["FILE", "INSTRUCTION", "FILE"]
                assert items[0].caption == "Oktyabr qiymətləri"
                assert [item.submitted_filename for item in items if item.file_object_id] == [
                    "prices.pdf",
                    "prices_copy.pdf",
                ]

            new_upload = await service.add_file(
                user, _metadata(storage, b"new bytes", 4, "new.pdf")
            )
            assert new_upload.summary.file_count == 1
            assert await service.cancel(user)
            assert not await service.cancel(user)
        finally:
            await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_reset(settings))


def test_exact_raw_bytes_reuse_one_blob_but_one_byte_change_is_new() -> None:
    """Synthetic source: filenames and appearances never override byte identity."""
    settings = _settings_or_skip()
    user = asyncio.run(_reset(settings))

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        storage = LocalFileStorage(Path(settings.file_storage_path) / "phase3-byte-dedup-test")
        service = IngestionSessionService(factory, storage)
        first_bytes = b"%PDF-1.4 synthetic product version A"
        changed_bytes = b"%PDF-1.4 synthetic product version B"
        assert len(first_bytes) == len(changed_bytes)
        try:
            first = await service.add_file(
                user, _metadata(storage, first_bytes, 301, "original.pdf")
            )
            second = await service.add_file(
                user, _metadata(storage, first_bytes, 302, "renamed.pdf")
            )
            third = await service.add_file(
                user, _metadata(storage, changed_bytes, 303, "original.pdf")
            )
            assert not first.duplicate
            assert second.duplicate
            assert not third.duplicate
            summary = await service.commit(user)
            assert summary is not None
            assert summary.file_count == 3
            assert summary.duplicate_count == 1
            async with factory() as session:
                objects = list((await session.scalars(select(FileObject))).all())
                items = list(
                    (
                        await session.scalars(
                            select(IngestionSessionItem).order_by(IngestionSessionItem.sequence_no)
                        )
                    ).all()
                )
                assert len(objects) == 2
                assert all(item.state == FileObjectState.ACTIVE for item in objects)
                assert items[0].file_object_id == items[1].file_object_id
                assert items[0].file_object_id != items[2].file_object_id
                assert [item.submitted_filename for item in items] == [
                    "original.pdf",
                    "renamed.pdf",
                    "original.pdf",
                ]
                stored_bytes = {
                    storage.path_for_key(item.storage_key).read_bytes() for item in objects
                }
                assert stored_bytes == {first_bytes, changed_bytes}
            assert len(tuple(storage.blob_root.iterdir())) == 2
        finally:
            await engine.dispose()
            rmtree(storage.root, ignore_errors=True)

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_reset(settings))


def test_same_digest_with_corrupted_existing_blob_fails_closed() -> None:
    """A forged/stale stored file is never reported as a safe exact duplicate."""
    settings = _settings_or_skip()
    user = asyncio.run(_reset(settings))

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        storage = LocalFileStorage(Path(settings.file_storage_path) / "phase3-integrity-test")
        service = IngestionSessionService(factory, storage)
        original = b"%PDF-1.4 synthetic immutable payload"
        try:
            await service.add_file(user, _metadata(storage, original, 401, "original.pdf"))
            assert await service.commit(user) is not None
            async with factory() as session:
                stored = (await session.scalars(select(FileObject))).one()
                path = storage.path_for_key(stored.storage_key)
            path.write_bytes(b"!" + original[1:])
            with pytest.raises(StorageError, match="integrity check"):
                await service.add_file(user, _metadata(storage, original, 402, "copy.pdf"))
            async with factory() as session:
                assert len((await session.scalars(select(FileObject))).all()) == 1
                assert len((await session.scalars(select(IngestionSessionItem))).all()) == 1
        finally:
            await engine.dispose()
            rmtree(storage.root, ignore_errors=True)

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_reset(settings))


def test_ingestion_authorization_and_expiration() -> None:
    settings = _settings_or_skip()
    user = asyncio.run(_reset(settings))

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        storage = LocalFileStorage(Path(settings.file_storage_path) / "phase3-expiry-test")
        service = IngestionSessionService(factory, storage, ttl_hours=1)
        try:
            pending = User(telegram_user_id=930002, role=UserRole.USER, status=UserStatus.PENDING)
            async with factory.begin() as session:
                session.add(pending)
                await session.flush()
            with pytest.raises(AuthorizationDenied):
                await service.add_file(pending, _metadata(storage, b"blocked", 20, "blocked.pdf"))

            await service.add_file(user, _metadata(storage, b"expire me", 21, "expire.pdf"))
            async with factory.begin() as session:
                await session.execute(
                    update(IngestionSession)
                    .where(IngestionSession.user_id == user.id)
                    .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
                )
            assert await service.cleanup_expired() == 1
            async with factory() as session:
                session_row = (await session.execute(select(IngestionSession))).scalar_one()
                file_row = (await session.execute(select(FileObject))).scalar_one()
                assert session_row.status == IngestionSessionStatus.EXPIRED
                assert file_row.state == FileObjectState.DELETED
        finally:
            await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_reset(settings))


def test_web_file_preview_only_commits_after_explicit_confirmation() -> None:
    settings = _settings_or_skip()
    user = asyncio.run(_reset(settings))

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        storage = LocalFileStorage(Path(settings.file_storage_path) / "miniapp-web-integration")
        ingestion = IngestionSessionService(factory, storage, max_files=10)
        workflow = MiniAppUploadWorkflow(storage, ingestion)
        try:
            path, key = storage.create_staging_path(".pdf")
            path.write_bytes(b"%PDF-1.4 sample approved by user")
            preview = await workflow.register(
                user,
                key=key,
                filename="example.pdf",
                mime="application/pdf",
                size=path.stat().st_size,
            )
            assert preview["status"] == "preview"
            async with factory() as session:
                assert (await session.execute(select(FileObject))).scalars().all() == []
                assert (await session.execute(select(IngestionJob))).scalars().all() == []
                assert (await session.execute(select(IngestionSession))).scalars().all() == []

            saved = await workflow.confirm(user, str(preview["token"]))
            assert saved["status"] == "queued"
            assert saved["job_id"] is not None
            async with factory() as session:
                files = (await session.execute(select(FileObject))).scalars().all()
                jobs = (await session.execute(select(IngestionJob))).scalars().all()
                sessions = (await session.execute(select(IngestionSession))).scalars().all()
                assert len(files) == 1
                assert files[0].state == FileObjectState.ACTIVE
                assert len(jobs) == 1
                assert jobs[0].status == IngestionJobStatus.PENDING
                assert len(sessions) == 1
                assert sessions[0].status == IngestionSessionStatus.COMMITTED
        finally:
            await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_reset(settings))


def test_web_commit_guard_does_not_save_extra_unreviewed_bot_notes() -> None:
    settings = _settings_or_skip()
    user = asyncio.run(_reset(settings))

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        storage = LocalFileStorage(Path(settings.file_storage_path) / "miniapp-guard-integration")
        ingestion = IngestionSessionService(factory, storage)
        try:
            staged = await ingestion.add_file(
                user,
                _metadata(storage, b"unapproved concurrent bot note", 505, "note.pdf"),
            )
            assert await ingestion.add_instruction(
                user,
                text_content="Other unsaved bot note",
                chat_id=user.telegram_user_id,
                message_id=506,
                update_id=999,
            )
            with pytest.raises(InvalidStateTransition):
                await ingestion.commit(
                    user,
                    expected_session_id=staged.summary.session_id,
                    expected_file_count=1,
                    expected_instruction_count=0,
                )
            async with factory() as session:
                files = (await session.execute(select(FileObject))).scalars().all()
                assert len(files) == 1
                assert files[0].state == FileObjectState.STAGED
                assert (await session.execute(select(IngestionJob))).scalars().all() == []
                entries = (await session.execute(select(IngestionSession))).scalars().all()
                assert entries[0].status == IngestionSessionStatus.OPEN
        finally:
            await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_reset(settings))


def test_miniapp_progress_only_uses_own_committed_ingestion_and_index_evidence() -> None:
    settings = _settings_or_skip()
    user = asyncio.run(_reset(settings))

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        storage = LocalFileStorage(Path(settings.file_storage_path) / "miniapp-status-integration")
        ingestion = IngestionSessionService(factory, storage, max_files=10)
        progress = MiniAppDocumentProgress(factory)
        try:
            created = await ingestion.add_file(
                user, _metadata(storage, b"%PDF-real-status-test", 702, "status.pdf")
            )
            committed = await ingestion.commit(user)
            assert committed is not None
            assert created.summary.session_id == committed.session_id
            assert committed.job_id is not None
            job_id = committed.job_id
            queued = await progress.one(user, job_id=job_id)
            assert queued["stage"] == "queued"
            assert queued["terminal"] is False
            assert queued["filename"] == "status.pdf"

            stranger = User(id=user.id + 10000, telegram_user_id=930009)
            with pytest.raises(UploadJobNotFound):
                await progress.one(stranger, job_id=job_id)
            assert await progress.recent(stranger) == []
            with pytest.raises(ValueError):
                await progress.one(user, job_id=True)

            async with factory.begin() as session:
                job = await session.get(IngestionJob, job_id, with_for_update=True)
                assert job is not None
                job.status = IngestionJobStatus.RUNNING
            assert (await progress.one(user, job_id=job_id))["stage"] == "reading"

            async with factory.begin() as session:
                job = await session.get(IngestionJob, job_id, with_for_update=True)
                assert job is not None
                job.status = IngestionJobStatus.SUCCEEDED
                file = await session.scalar(select(FileObject))
                assert file is not None
                parse = DocumentParseRun(
                    file_object_id=file.id,
                    ingestion_job_id=job_id,
                    status=DocumentProcessingStatus.SUCCEEDED,
                    parser_name="test-fixture",
                    parser_version="v1",
                    requires_ocr=False,
                    requires_vision=False,
                    warning_count=0,
                )
                session.add(parse)
                await session.flush()
                parse_id = parse.id
            assert (await progress.one(user, job_id=job_id))["stage"] == "indexing"

            async with factory.begin() as session:
                index = KnowledgeIndexJob(
                    ingestion_job_id=job_id,
                    status=KnowledgeIndexJobStatus.SUCCEEDED,
                    attempt_count=1,
                    collection_alias="documents",
                    collection_name="documents_test",
                    embedding_model="test",
                    chunking_version="test",
                )
                session.add(index)
                await session.flush()
                index_id = index.id
            # An index-job success flag with no proven indexed chunks is not ready.
            assert (await progress.one(user, job_id=job_id))["stage"] == "review"

            async with factory.begin() as session:
                record = KnowledgeIndexRecord(
                    knowledge_index_job_id=index_id,
                    parse_run_id=parse_id,
                    status=KnowledgeIndexRecordStatus.SUCCEEDED,
                    collection_name="documents_test",
                    embedding_model="test",
                    chunking_version="test",
                    chunk_count=2,
                )
                session.add(record)
            ready = await progress.one(user, job_id=job_id)
            assert ready["stage"] == "ready"
            assert ready["terminal"] is True
            assert [item["job_id"] for item in await progress.recent(user)] == [job_id]
        finally:
            await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_reset(settings))
