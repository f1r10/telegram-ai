import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, select, update

from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.ingestion import IngestionSessionService, UploadMetadata
from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    FileObject,
    FileObjectState,
    IngestionJob,
    IngestionJobStatus,
    IngestionSession,
    IngestionSessionItem,
    IngestionSessionStatus,
    SystemMetadata,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage

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
