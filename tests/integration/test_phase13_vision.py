"""An uploaded photo is stored and stays visibly deferred; no bytes go to a vendor (ADR 0017)."""

import asyncio
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AIConversation,
    AIMessage,
    AIUsage,
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
    OutboxEvent,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.processing.worker_service import IngestionJobService

pytestmark = pytest.mark.integration

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 128


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


def test_photo_upload_defers_without_reading_a_byte_of_vision_egress() -> None:
    """The worker records the photo and stops; a vendor call would have to be re-added here."""

    settings = _settings_or_skip()
    root = Path(settings.file_storage_path) / "phase13-defer-test"
    storage = LocalFileStorage(root)

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)

        async def clean() -> None:
            async with factory.begin() as session:
                for table in (
                    OutboxEvent,
                    AuditEvent,
                    AIUsage,
                    AIMessage,
                    AIConversation,
                    KnowledgeIndexJob,
                    DocumentParseRun,
                    IngestionJob,
                    IngestionSessionItem,
                    IngestionSession,
                    FileObject,
                    User,
                ):
                    await session.execute(delete(table))

        try:
            await clean()
            async with factory.begin() as session:
                user = User(telegram_user_id=940113, role=UserRole.USER, status=UserStatus.ACTIVE)
                session.add(user)
                await session.flush()
                ingestion_session = IngestionSession(
                    user_id=user.id,
                    status=IngestionSessionStatus.COMMITTED,
                    expires_at=datetime.now(UTC),
                )
                session.add(ingestion_session)
                await session.flush()
                staged_path, staged_key = storage.create_staging_path(".jpg")
                staged_path.write_bytes(JPEG_BYTES)
                file_object = FileObject(
                    storage_key=storage.activate(staged_key),
                    original_filename="telegram_photo_940113.jpg",
                    content_type="image/jpeg",
                    byte_size=len(JPEG_BYTES),
                    sha256="b" * 64,
                    state=FileObjectState.ACTIVE,
                )
                session.add(file_object)
                await session.flush()
                session.add(
                    IngestionSessionItem(
                        session_id=ingestion_session.id,
                        item_type=IngestionItemType.FILE,
                        file_object_id=file_object.id,
                        submitted_filename="telegram_photo_940113.jpg",
                        sender_user_id=user.id,
                        telegram_chat_id=940113,
                        telegram_message_id=1,
                        sequence_no=1,
                    )
                )
                job = IngestionJob(
                    session_id=ingestion_session.id,
                    status=IngestionJobStatus.PENDING,
                    attempt_count=0,
                )
                session.add(job)
                await session.flush()
                job_id = job.id

            service = IngestionJobService(factory, storage)
            claim = await service.claim_next("defer-worker")
            assert claim is not None
            await service.process(claim)

            async with factory() as session:
                run = (
                    await session.execute(
                        select(DocumentParseRun).where(DocumentParseRun.ingestion_job_id == job_id)
                    )
                ).scalar_one()
                job_row = await session.get(IngestionJob, job_id)
                assert job_row is not None
                # The upload itself is a success: the file is kept and the reason is recorded.
                assert job_row.status == IngestionJobStatus.SUCCEEDED
                assert run.status == DocumentProcessingStatus.DEFERRED
                assert run.requires_vision is True
                assert run.parser_name == "image-probe"
                assert run.text_storage_key is not None
                assert storage.path_for_key(run.text_storage_key).read_text("utf-8") == ""
                normalized = json.loads(
                    storage.path_for_key(run.normalized_storage_key or "").read_text("utf-8")
                )
                # No content blocks means the normal chunk/embed path has nothing to index, so
                # the photo cannot answer a /baza question until it is read on demand for one.
                assert normalized["blocks"] == []
        finally:
            await clean()
            await engine.dispose()
            shutil.rmtree(root, ignore_errors=True)

    asyncio.run(scenario())
