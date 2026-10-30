"""Disposable PostgreSQL proof: photo groups preserve sources and actor isolation."""

import asyncio
from pathlib import Path
from shutil import rmtree

import pytest
from sqlalchemy import delete, select

from office_assistant.application.errors import AuthorizationDenied, InvalidStateTransition
from office_assistant.application.ingestion import IngestionSessionService, UploadMetadata
from office_assistant.application.photo_groups import PhotoGroupService
from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    FileObject,
    IngestionJob,
    IngestionSession,
    IngestionSessionItem,
    PhotoGroup,
    PhotoGroupImage,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage

pytestmark = pytest.mark.integration
ACTOR = 953101
OTHER = 953102


def _settings() -> Settings:
    settings = load_settings()
    if not all(
        (
            settings.postgres_host,
            settings.postgres_db,
            settings.postgres_user,
            settings.postgres_password,
        )
    ):
        pytest.skip("Disposable PostgreSQL not configured")
    return settings


async def _clean(settings: Settings) -> None:
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory.begin() as session:
            ids = select(User.id).where(User.telegram_user_id.in_([ACTOR, OTHER]))
            files = list(
                (
                    await session.scalars(
                        select(IngestionSessionItem.file_object_id).where(
                            IngestionSessionItem.sender_user_id.in_(ids)
                        )
                    )
                ).all()
            )
            groups = select(PhotoGroup.id).where(PhotoGroup.owner_user_id.in_(ids))
            await session.execute(
                delete(PhotoGroupImage).where(PhotoGroupImage.group_id.in_(groups))
            )
            await session.execute(delete(PhotoGroup).where(PhotoGroup.owner_user_id.in_(ids)))
            await session.execute(
                delete(IngestionJob).where(
                    IngestionJob.session_id.in_(
                        select(IngestionSession.id).where(IngestionSession.user_id.in_(ids))
                    )
                )
            )
            await session.execute(
                delete(IngestionSessionItem).where(IngestionSessionItem.sender_user_id.in_(ids))
            )
            await session.execute(
                delete(IngestionSession).where(IngestionSession.user_id.in_(ids))
            )
            if files:
                await session.execute(
                    delete(FileObject).where(FileObject.id.in_(files))
                )
            await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(ids)))
            await session.execute(delete(User).where(User.telegram_user_id.in_([ACTOR, OTHER])))
    finally:
        await engine.dispose()


def _upload(
    storage: LocalFileStorage,
    *,
    body: bytes,
    message_id: int,
) -> UploadMetadata:
    staged_path, staged_key = storage.create_staging_path(".jpg")
    staged_path.write_bytes(body)
    return UploadMetadata(
        original_filename=f"photo_{message_id}.jpg",
        content_type="image/jpeg",
        staging_path=staged_path,
        staging_key=staged_key,
        telegram_chat_id=ACTOR,
        telegram_message_id=message_id,
        telegram_update_id=message_id + 100,
        caption=None,
    )


def test_photo_group_create_edit_append_and_other_actor_denial() -> None:
    settings = _settings()
    asyncio.run(_clean(settings))

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        storage = LocalFileStorage(Path(settings.file_storage_path) / "v1-photo-group-ci")
        try:
            async with factory.begin() as session:
                actor = User(telegram_user_id=ACTOR, role=UserRole.USER, status=UserStatus.ACTIVE)
                other = User(telegram_user_id=OTHER, role=UserRole.USER, status=UserStatus.ACTIVE)
                session.add_all([actor, other])
                await session.flush()
            intake = IngestionSessionService(factory, storage)
            groups = PhotoGroupService(factory)
            await intake.add_file(actor, _upload(storage, body=b"first-photo", message_id=801))
            saved = await intake.commit(actor, photo_group_description="Ad: Qara saat")
            assert saved is not None and saved.photo_group_id is not None
            gid = saved.photo_group_id
            first = await groups.get(ACTOR, gid)
            assert first is not None and first.image_count == 1
            assert first.revision == 1
            assert await groups.get(OTHER, gid) is None
            with pytest.raises(AuthorizationDenied):
                await groups.update(OTHER, gid, "Ad: Başqasının qrupu")
            changed = await groups.update(
                ACTOR, gid, "Ad: Yeni ad\nTəsvir: Ön və yan bucaq"
            )
            assert changed.revision == 2
            assert "Yeni ad" in changed.description
            await intake.add_file(actor, _upload(storage, body=b"second-photo", message_id=802))
            appended = await intake.commit(actor, append_photo_group_id=gid)
            assert appended is not None and appended.photo_group_id == gid
            current = await groups.get(ACTOR, gid)
            assert current is not None and current.image_count == 2
            assert current.revision == 2
            assert len(await groups.list_own(ACTOR)) == 1
            assert await groups.list_own(OTHER) == []
            await intake.add_file(actor, _upload(storage, body=b"first-photo", message_id=803))
            with pytest.raises(InvalidStateTransition):
                await intake.commit(actor, append_photo_group_id=gid)
            assert await intake.cancel(actor)
            assert (await groups.get(ACTOR, gid)).image_count == 2
        finally:
            await engine.dispose()
            rmtree(storage.root, ignore_errors=True)

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_clean(settings))
