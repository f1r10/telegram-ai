import asyncio
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


def test_two_workers_cannot_claim_the_same_job_and_processing_is_durable() -> None:
    settings = _settings_or_skip()
    root = Path(settings.file_storage_path) / "phase4-worker-test"
    storage = LocalFileStorage(root)

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        try:
            async with factory.begin() as session:
                await session.execute(delete(OutboxEvent))
                await session.execute(delete(AuditEvent))
                await session.execute(delete(AIUsage))
                await session.execute(delete(AIMessage))
                await session.execute(delete(AIConversation))
                await session.execute(delete(KnowledgeIndexJob))
                await session.execute(delete(DocumentParseRun))
                await session.execute(delete(IngestionJob))
                await session.execute(delete(IngestionSessionItem))
                await session.execute(delete(IngestionSession))
                await session.execute(delete(FileObject))
                await session.execute(delete(User))
                user = User(
                    telegram_user_id=940001,
                    role=UserRole.USER,
                    status=UserStatus.ACTIVE,
                )
                session.add(user)
                await session.flush()
                session_row = IngestionSession(
                    user_id=user.id,
                    status=IngestionSessionStatus.COMMITTED,
                    expires_at=datetime.now(UTC),
                )
                session.add(session_row)
                await session.flush()
                staged_path, staged_key = storage.create_staging_path(".pdf")
                raw_pdf = _make_pdf("worker content")
                staged_path.write_bytes(raw_pdf)
                active_key = storage.activate(staged_key)
                file_object = FileObject(
                    storage_key=active_key,
                    original_filename="worker.pdf",
                    content_type="application/pdf",
                    byte_size=len(raw_pdf),
                    sha256="a" * 64,
                    state=FileObjectState.ACTIVE,
                )
                session.add(file_object)
                await session.flush()
                session.add(
                    IngestionSessionItem(
                        session_id=session_row.id,
                        item_type=IngestionItemType.FILE,
                        file_object_id=file_object.id,
                        submitted_filename="worker.pdf",
                        sender_user_id=user.id,
                        telegram_chat_id=940001,
                        telegram_message_id=1,
                        sequence_no=1,
                    )
                )
                job = IngestionJob(
                    session_id=session_row.id,
                    status=IngestionJobStatus.PENDING,
                    attempt_count=0,
                )
                session.add(job)
                await session.flush()
                job_id = job.id

            first, second = await asyncio.gather(
                IngestionJobService(factory, storage).claim_next("worker-one"),
                IngestionJobService(factory, storage).claim_next("worker-two"),
            )
            claims = [claim for claim in (first, second) if claim is not None]
            assert len(claims) == 1
            await IngestionJobService(factory, storage).process(claims[0])

            async with factory() as session:
                job_row = await session.get(IngestionJob, job_id)
                result = (await session.execute(select(DocumentParseRun))).scalar_one()
                outbox = (await session.execute(select(OutboxEvent))).scalar_one()
                assert job_row is not None
                assert job_row.status == IngestionJobStatus.SUCCEEDED
                assert result.status == DocumentProcessingStatus.SUCCEEDED
                assert storage.exists(result.normalized_storage_key or "")
                assert storage.path_for_key(active_key).read_bytes() == raw_pdf
                assert (
                    "worker content"
                    in storage.path_for_key(result.text_storage_key or "").read_text()
                )
                assert outbox.aggregate_id == str(job_id)
        finally:
            await engine.dispose()
            shutil.rmtree(root, ignore_errors=True)

    asyncio.run(scenario())


def _make_pdf(text: str) -> bytes:
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        (
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj"
        ),
        f"4 0 obj << /Length {len(content)} >> stream\n".encode() + content + b"\nendstream endobj",
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(output))
        output.extend(obj + b"\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        (
            f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(output)
