"""Uploaded photos become searchable document text, or stay visibly deferred."""

import asyncio
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from office_assistant.ai.provider import ModelResponse, TokenUsage
from office_assistant.ai.schemas import ImageTranscriptionOutput
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
from office_assistant.processing.vision import ImageVisionExtractor
from office_assistant.processing.worker_service import IngestionJobService

pytestmark = pytest.mark.integration

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 128

# The stub payload and the assertions share one source, so the test compares the bytes the
# pipeline moved rather than re-typing them; the derived artifact is UTF-8 by contract.
TRANSCRIBED_TEXT = "KA45 — 125 AZN"
TRANSCRIBED_DESCRIPTION = "Qiymət siyahısı şəkli"


class StubVisionModel:
    def __init__(self, result: ImageTranscriptionOutput | None, error: Exception | None) -> None:
        self.result = result
        self.error = error

    async def describe_image(self, **kwargs: object) -> ModelResponse:
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return ModelResponse(
            parsed=self.result,
            model="stub-vision",
            response_id=None,
            usage=TokenUsage(),
            latency_ms=5,
        )


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


def test_photo_transcribed_by_vision_is_indexed_as_document_text() -> None:
    settings = _settings_or_skip()
    root = Path(settings.file_storage_path) / "phase13-vision-test"
    model = StubVisionModel(
        ImageTranscriptionOutput(
            transcription=TRANSCRIBED_TEXT, description=TRANSCRIBED_DESCRIPTION
        ),
        None,
    )
    _run(settings, root, model, DocumentProcessingStatus.SUCCEEDED)


def test_vision_failure_leaves_the_photo_deferred_not_failed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings_or_skip()
    root = Path(settings.file_storage_path) / "phase13-vision-failure-test"
    model = StubVisionModel(None, RuntimeError("vision vendor is down"))

    with caplog.at_level(logging.WARNING):
        _run(settings, root, model, DocumentProcessingStatus.DEFERRED)

    assert "Vision extraction unavailable" in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def _run(
    settings: Settings, root: Path, model: StubVisionModel, expected: DocumentProcessingStatus
) -> None:
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
                active_key = storage.activate(staged_key)
                file_object = FileObject(
                    storage_key=active_key,
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

            extractor = ImageVisionExtractor(model, max_image_bytes=4 * 1024 * 1024)
            service = IngestionJobService(factory, storage, vision=extractor)
            claim = await service.claim_next("vision-worker")
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
                assert job_row.status == IngestionJobStatus.SUCCEEDED
                assert run.status == expected
                assert run.requires_vision is True
                assert run.normalized_storage_key is not None
                text = storage.path_for_key(run.text_storage_key or "").read_text(encoding="utf-8")
                if expected == DocumentProcessingStatus.SUCCEEDED:
                    assert run.parser_name == "vision-transcription"
                    assert run.parser_version == "stub-vision"
                    assert TRANSCRIBED_TEXT in text
                    assert TRANSCRIBED_DESCRIPTION in text
                else:
                    assert run.parser_name == "image-probe"
                    assert text == ""
        finally:
            await clean()
            await engine.dispose()
            shutil.rmtree(root, ignore_errors=True)

    asyncio.run(scenario())
