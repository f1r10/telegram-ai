"""File bytes require a second explicit action before entering company ingestion."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from office_assistant.application.ingestion import (
    IngestionSessionService,
    SessionSummary,
    UploadResult,
)
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.miniapp.uploads import (
    MiniAppUploadConflict,
    MiniAppUploadExpired,
    MiniAppUploadWorkflow,
)


def setup(tmp_path: Path) -> tuple[MiniAppUploadWorkflow, LocalFileStorage, AsyncMock, User]:
    storage = LocalFileStorage(tmp_path / "company")
    ingestion = AsyncMock(spec=IngestionSessionService)
    ingestion.has_open_session.return_value = False
    ingestion.add_file.return_value = UploadResult(
        SessionSummary(session_id=18, file_count=1, instruction_count=0, duplicate_count=0),
        duplicate=False,
    )
    ingestion.commit.return_value = SessionSummary(
        session_id=18, file_count=1, instruction_count=0, duplicate_count=0, job_id=77
    )
    actor = User(id=9, telegram_user_id=123, role=UserRole.USER, status=UserStatus.ACTIVE)
    return (
        MiniAppUploadWorkflow(storage, cast(IngestionSessionService, ingestion)),
        storage,
        ingestion,
        actor,
    )


def write_staged(storage: LocalFileStorage, data: bytes = b"%PDF-sample") -> tuple[Path, str]:
    path, key = storage.create_staging_path(".pdf")
    path.write_bytes(data)
    return path, key


def test_preview_does_not_add_file_or_create_ingestion_job(tmp_path: Path) -> None:
    workflow, storage, ingestion, actor = setup(tmp_path)
    path, key = write_staged(storage)
    preview = asyncio.run(
        workflow.register(
            actor,
            key=key,
            filename="contract.pdf",
            mime="application/pdf",
            size=path.stat().st_size
        )
    )
    assert preview["status"] == "preview"
    assert path.is_file()
    ingestion.add_file.assert_not_awaited()
    ingestion.commit.assert_not_awaited()
    result = asyncio.run(workflow.cancel(actor, cast(str, preview["token"])))
    assert result["status"] == "cancelled"
    assert not path.exists()
    ingestion.add_file.assert_not_awaited()


def test_explicit_confirmation_reuses_ingestion_and_fences_reviewed_session(tmp_path: Path) -> None:
    workflow, storage, ingestion, actor = setup(tmp_path)
    path, key = write_staged(storage)
    preview = asyncio.run(
        workflow.register(
            actor,
            key=key,
            filename="contract.pdf",
            mime="application/pdf",
            size=path.stat().st_size
        )
    )
    token = cast(str, preview["token"])
    result = asyncio.run(workflow.confirm(actor, token))
    assert result["status"] == "queued"
    assert result["job_id"] == 77
    meta = ingestion.add_file.await_args.args[1]
    assert meta.staging_key == key
    assert meta.original_filename == "contract.pdf"
    assert meta.telegram_chat_id < -10**15
    assert meta.telegram_message_id > 0
    ingestion.commit.assert_awaited_once_with(
        actor,
        expected_session_id=18,
        expected_file_count=1,
        expected_instruction_count=0,
    )
    with pytest.raises(MiniAppUploadExpired):
        asyncio.run(workflow.confirm(actor, token))


def test_existing_bot_session_blocks_preview_and_commit(tmp_path: Path) -> None:
    workflow, storage, ingestion, actor = setup(tmp_path)
    ingestion.has_open_session.return_value = True
    with pytest.raises(MiniAppUploadConflict):
        asyncio.run(workflow.permission(actor))
    path, key = write_staged(storage)
    with pytest.raises(MiniAppUploadConflict):
        asyncio.run(
            workflow.register(
                actor,
            key=key,
            filename="contract.pdf",
            mime="application/pdf",
            size=path.stat().st_size
            )
        )
    assert not path.exists()
    ingestion.add_file.assert_not_awaited()


def test_uploaded_bytes_must_match_preview_and_unauthorized_actor_cannot_confirm(
    tmp_path: Path,
) -> None:
    workflow, storage, ingestion, actor = setup(tmp_path)
    path, key = write_staged(storage)
    preview = asyncio.run(
        workflow.register(
            actor,
            key=key,
            filename="contract.pdf",
            mime="application/pdf",
            size=path.stat().st_size
        )
    )
    token = cast(str, preview["token"])
    other = User(id=10, telegram_user_id=555, role=UserRole.USER, status=UserStatus.ACTIVE)
    with pytest.raises(MiniAppUploadExpired):
        asyncio.run(workflow.confirm(other, token))
    path.write_bytes(b"%PDF-changed")
    with pytest.raises(MiniAppUploadExpired):
        asyncio.run(workflow.confirm(actor, token))
    ingestion.add_file.assert_not_awaited()
    assert not path.exists()


def test_simultaneous_bot_upload_cannot_be_implicitly_committed(tmp_path: Path) -> None:
    workflow, storage, ingestion, actor = setup(tmp_path)
    path, key = write_staged(storage)
    preview = asyncio.run(
        workflow.register(
            actor,
            key=key,
            filename="contract.pdf",
            mime="application/pdf",
            size=path.stat().st_size
        )
    )
    # Existing session appears before confirmation.
    ingestion.has_open_session.return_value = True
    with pytest.raises(MiniAppUploadConflict):
        asyncio.run(workflow.confirm(actor, cast(str, preview["token"])))
    ingestion.add_file.assert_not_awaited()
    ingestion.commit.assert_not_awaited()


def test_concurrent_addition_causes_pre_save_fence_not_auto_commit(tmp_path: Path) -> None:
    workflow, storage, ingestion, actor = setup(tmp_path)
    path, key = write_staged(storage)
    preview = asyncio.run(
        workflow.register(
            actor,
            key=key,
            filename="contract.pdf",
            mime="application/pdf",
            size=path.stat().st_size
        )
    )
    ingestion.add_file.return_value = UploadResult(
        SessionSummary(session_id=18, file_count=2, instruction_count=0, duplicate_count=0),
        duplicate=False,
    )
    with pytest.raises(MiniAppUploadConflict):
        asyncio.run(workflow.confirm(actor, cast(str, preview["token"])))
    ingestion.commit.assert_not_awaited()


def test_upload_size_mime_filename_and_expiration_are_strict(tmp_path: Path) -> None:
    workflow, storage, ingestion, actor = setup(tmp_path)
    for filename, mime, size in (
        ("../x.pdf", "application/pdf", 100),
        ("x.exe", "application/octet-stream", 100),
        ("x.pdf", "text/html", 100),
        ("x.pdf", "application/pdf", 8 * 1024 * 1024 + 1),
        ("x.pdf", "application/pdf", 0),
        (".secret.pdf", "application/pdf", 10),
    ):
        with pytest.raises(ValueError):
            workflow.validated_name(filename, mime, size)
    path, key = write_staged(storage)
    preview = asyncio.run(
        workflow.register(
            actor,
            key=key,
            filename="contract.pdf",
            mime="application/pdf",
            size=path.stat().st_size
        )
    )
    token = cast(str, preview["token"])
    existing = workflow._previews[token]  # pyright: ignore[reportPrivateUsage]
    workflow._previews[token] = replace(  # pyright: ignore[reportPrivateUsage]
        existing, expires_at=-1
    )
    asyncio.run(workflow.permission(actor))
    assert not path.exists()
    with pytest.raises(MiniAppUploadExpired):
        asyncio.run(workflow.confirm(actor, token))
    ingestion.add_file.assert_not_awaited()
