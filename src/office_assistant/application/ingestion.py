"""Pending raw-file ingestion sessions and the /saxla commit boundary.

``/saxla`` is the only place this package writes authoritative state, which matters for picture
confirmations: a caption that names a product queues a claim inside the open session, and the claim
becomes a relation only when the session commits. ``/cancel`` and expiry drop the claim with the
staged file, so nothing a user retracted can be read later as a business fact (Master Spec §23.7).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.errors import AuthorizationDenied, InvalidStateTransition
from office_assistant.application.reference_images import promote_queued_confirmations
from office_assistant.auth.policy import is_active
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    FileObject,
    FileObjectState,
    IngestionItemType,
    IngestionJob,
    IngestionJobStatus,
    IngestionSession,
    IngestionSessionItem,
    IngestionSessionStatus,
    User,
)
from office_assistant.infrastructure.storage.local import LocalFileStorage, StorageError
from office_assistant.repositories.pending_product_images import PendingProductImageRepository


@dataclass(frozen=True, slots=True)
class UploadMetadata:
    original_filename: str
    content_type: str
    staging_path: Path
    staging_key: str
    telegram_chat_id: int
    telegram_message_id: int
    telegram_update_id: int | None
    caption: str | None


@dataclass(frozen=True, slots=True)
class SessionSummary:
    session_id: int
    file_count: int
    instruction_count: int
    duplicate_count: int
    job_id: int | None = None
    reference_images_linked: int = 0


@dataclass(frozen=True, slots=True)
class UploadResult:
    summary: SessionSummary
    duplicate: bool


class IngestionSessionService:
    """Owns session state, file metadata, jobs, and filesystem compensation."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        *,
        max_files: int = 100,
        ttl_hours: int = 24,
        max_upload_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.max_files = max_files
        self.ttl = timedelta(hours=ttl_hours)
        self.max_upload_bytes = max_upload_bytes

    async def add_file(self, user: User, metadata: UploadMetadata) -> UploadResult:
        self._require_active(user)
        sha256, byte_size = self._hash(metadata.staging_path)
        moved_staging = True
        try:
            async with self.session_factory.begin() as session:
                db_user = await self._active_user(session, user.telegram_user_id)
                ingestion_session, created = await self._open_session(session, db_user.id)
                existing_item = await self._item_for_message(
                    session, metadata.telegram_chat_id, metadata.telegram_message_id
                )
                if existing_item is not None:
                    self.storage.delete(metadata.staging_key)
                    moved_staging = False
                    return await self._upload_result(session, ingestion_session.id, duplicate=False)

                file_count = await self._file_count(session, ingestion_session.id)
                if file_count >= self.max_files:
                    raise InvalidStateTransition("Pending session file limit reached")

                file_object = await self._file_by_sha(session, sha256, for_update=True)
                duplicate = file_object is not None
                if file_object is None:
                    file_object = FileObject(
                        storage_key=metadata.staging_key,
                        original_filename=metadata.original_filename,
                        content_type=metadata.content_type,
                        byte_size=byte_size,
                        sha256=sha256,
                        state=FileObjectState.STAGED,
                    )
                    session.add(file_object)
                    await session.flush()
                elif file_object.state == FileObjectState.DELETED:
                    file_object.storage_key = metadata.staging_key
                    file_object.original_filename = metadata.original_filename
                    file_object.content_type = metadata.content_type
                    file_object.byte_size = byte_size
                    file_object.state = FileObjectState.STAGED
                    file_object.activated_at = None
                else:
                    self.storage.delete(metadata.staging_key)
                    moved_staging = False

                sequence_no = await self._next_sequence(session, ingestion_session.id)
                session.add(
                    IngestionSessionItem(
                        session_id=ingestion_session.id,
                        item_type=IngestionItemType.FILE,
                        file_object_id=file_object.id,
                        submitted_filename=metadata.original_filename,
                        caption=metadata.caption,
                        sender_user_id=db_user.id,
                        telegram_chat_id=metadata.telegram_chat_id,
                        telegram_message_id=metadata.telegram_message_id,
                        telegram_update_id=metadata.telegram_update_id,
                        sequence_no=sequence_no,
                    )
                )
                self._audit(
                    session,
                    actor_user_id=db_user.id,
                    action="INGESTION_FILE_STAGED" if not duplicate else "INGESTION_FILE_DUPLICATE",
                    entity_type="file_object",
                    entity_id=str(file_object.id),
                )
                if created:
                    self._audit(
                        session,
                        actor_user_id=db_user.id,
                        action="INGESTION_SESSION_CREATED",
                        entity_type="ingestion_session",
                        entity_id=str(ingestion_session.id),
                    )
                await session.flush()
                result = await self._upload_result(
                    session, ingestion_session.id, duplicate=duplicate
                )
                return result
        except Exception:
            if moved_staging:
                self._delete_quietly(metadata.staging_key)
            raise

    async def add_instruction(
        self,
        user: User,
        *,
        text_content: str,
        chat_id: int,
        message_id: int,
        update_id: int | None,
        create_session: bool = False,
    ) -> bool:
        self._require_active(user)
        if not text_content.strip():
            return False
        async with self.session_factory.begin() as session:
            db_user = await self._active_user(session, user.telegram_user_id)
            ingestion_session = await self._existing_open_session(session, db_user.id)
            if ingestion_session is None:
                if not create_session:
                    return False
                ingestion_session, _ = await self._open_session(session, db_user.id)
            if await self._item_for_message(session, chat_id, message_id) is not None:
                return True
            sequence_no = await self._next_sequence(session, ingestion_session.id)
            session.add(
                IngestionSessionItem(
                    session_id=ingestion_session.id,
                    item_type=IngestionItemType.INSTRUCTION,
                    text_content=text_content,
                    sender_user_id=db_user.id,
                    telegram_chat_id=chat_id,
                    telegram_message_id=message_id,
                    telegram_update_id=update_id,
                    sequence_no=sequence_no,
                )
            )
            self._audit(
                session,
                actor_user_id=db_user.id,
                action="INGESTION_INSTRUCTION_ADDED",
                entity_type="ingestion_session",
                entity_id=str(ingestion_session.id),
            )
            return True

    async def has_open_session(self, user: User) -> bool:
        """Check the session priority boundary without mutating ingestion state."""

        self._require_active(user)
        async with self.session_factory() as session:
            db_user = await self._active_user(session, user.telegram_user_id)
            return await self._existing_open_session(session, db_user.id) is not None

    async def commit(
        self,
        user: User,
        *,
        expected_session_id: int | None = None,
        expected_file_count: int | None = None,
        expected_instruction_count: int | None = None,
    ) -> SessionSummary | None:
        """Optional safety fence for web approval of exactly the reviewed items."""

        self._require_active(user)
        moved: list[tuple[str, str]] = []
        try:
            async with self.session_factory.begin() as session:
                db_user = await self._active_user(session, user.telegram_user_id)
                ingestion_session = await self._existing_open_session(
                    session, db_user.id, for_update=True
                )
                if ingestion_session is None:
                    return None
                items = await self._items(session, ingestion_session.id)
                file_items = [item for item in items if item.item_type == IngestionItemType.FILE]
                if expected_session_id is not None and ingestion_session.id != expected_session_id:
                    raise InvalidStateTransition("The reviewed session was replaced")
                if expected_file_count is not None and len(file_items) != expected_file_count:
                    raise InvalidStateTransition("The reviewed file list changed")
                if (
                    expected_instruction_count is not None
                    and len(items) - len(file_items) != expected_instruction_count
                ):
                    raise InvalidStateTransition("The reviewed notes changed")
                if not items:
                    raise InvalidStateTransition("A session must contain at least one item")
                for item in file_items:
                    if item.file_object_id is None:
                        raise InvalidStateTransition("File item has no file object")
                    file_object = await session.get(
                        FileObject, item.file_object_id, with_for_update=True
                    )
                    if file_object is None:
                        raise InvalidStateTransition("File object is missing")
                    if file_object.state == FileObjectState.STAGED:
                        old_key = file_object.storage_key
                        new_key = self.storage.activate(old_key)
                        moved.append((old_key, new_key))
                        file_object.storage_key = new_key
                        file_object.state = FileObjectState.ACTIVE
                        file_object.activated_at = datetime.now(UTC)
                        self._audit(
                            session,
                            actor_user_id=db_user.id,
                            action="INGESTION_FILE_COMMITTED",
                            entity_type="file_object",
                            entity_id=str(file_object.id),
                        )
                    elif file_object.state != FileObjectState.ACTIVE:
                        raise InvalidStateTransition("File object cannot be committed")

                # The picture claims this session's captions queued become relations here, in the
                # same transaction as the files they speak about. A refusal rolls the commit back
                # rather than saving a file whose confirmation was silently dropped (§23.7).
                reference_images_linked = await promote_queued_confirmations(
                    session,
                    self.storage,
                    session_id=ingestion_session.id,
                    actor_user_id=db_user.id,
                )

                job = await session.scalar(
                    select(IngestionJob).where(IngestionJob.session_id == ingestion_session.id)
                )
                if job is None:
                    job = IngestionJob(
                        session_id=ingestion_session.id,
                        status=IngestionJobStatus.PENDING,
                        attempt_count=0,
                    )
                    session.add(job)
                    await session.flush()
                ingestion_session.status = IngestionSessionStatus.COMMITTED
                ingestion_session.committed_at = datetime.now(UTC)
                self._audit(
                    session,
                    actor_user_id=db_user.id,
                    action="INGESTION_SESSION_COMMITTED",
                    entity_type="ingestion_session",
                    entity_id=str(ingestion_session.id),
                )
                return SessionSummary(
                    session_id=ingestion_session.id,
                    file_count=len(file_items),
                    instruction_count=sum(
                        item.item_type == IngestionItemType.INSTRUCTION for item in items
                    ),
                    duplicate_count=len(file_items)
                    - len({item.file_object_id for item in file_items}),
                    job_id=job.id,
                    reference_images_linked=reference_images_linked,
                )
        except Exception:
            for old_key, new_key in reversed(moved):
                try:
                    self.storage.restore(new_key, old_key)
                except StorageError:
                    pass
            raise

    async def cancel(self, user: User) -> bool:
        self._require_active(user)
        delete_keys: list[str] = []
        async with self.session_factory.begin() as session:
            db_user = await self._active_user(session, user.telegram_user_id)
            ingestion_session = await self._existing_open_session(
                session, db_user.id, for_update=True
            )
            if ingestion_session is None:
                return False
            items = await self._items(session, ingestion_session.id)
            for item in items:
                if item.file_object_id is None:
                    continue
                file_object = await session.get(
                    FileObject, item.file_object_id, with_for_update=True
                )
                if file_object is None or file_object.state != FileObjectState.STAGED:
                    continue
                other_reference = await session.scalar(
                    select(IngestionSessionItem.id).where(
                        IngestionSessionItem.file_object_id == file_object.id,
                        IngestionSessionItem.session_id != ingestion_session.id,
                    )
                )
                if other_reference is None:
                    file_object.state = FileObjectState.DELETED
                    delete_keys.append(file_object.storage_key)
            ingestion_session.status = IngestionSessionStatus.CANCELLED
            cancelled_at = datetime.now(UTC)
            ingestion_session.cancelled_at = cancelled_at
            # A caption that named a product queued a claim, not a fact: discarding the session has
            # to discard the claim with it, so /cancel never leaves a relation behind (§23.7).
            await PendingProductImageRepository(session).cancel_for_session(
                ingestion_session.id, at=cancelled_at
            )
            self._audit(
                session,
                actor_user_id=db_user.id,
                action="INGESTION_SESSION_CANCELLED",
                entity_type="ingestion_session",
                entity_id=str(ingestion_session.id),
            )
        for key in delete_keys:
            self._delete_quietly(key)
        return True

    async def cleanup_expired(self, *, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        delete_keys: list[str] = []
        async with self.session_factory.begin() as session:
            expired = list(
                (
                    await session.execute(
                        select(IngestionSession)
                        .where(
                            IngestionSession.status == IngestionSessionStatus.OPEN,
                            IngestionSession.expires_at <= current,
                        )
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            for ingestion_session in expired:
                ingestion_session.status = IngestionSessionStatus.EXPIRED
                items = await self._items(session, ingestion_session.id)
                for item in items:
                    if item.file_object_id is None:
                        continue
                    file_object = await session.get(
                        FileObject, item.file_object_id, with_for_update=True
                    )
                    if file_object is None or file_object.state != FileObjectState.STAGED:
                        continue
                    other_reference = await session.scalar(
                        select(IngestionSessionItem.id).where(
                            IngestionSessionItem.file_object_id == file_object.id,
                            IngestionSessionItem.session_id != ingestion_session.id,
                        )
                    )
                    if other_reference is None:
                        file_object.state = FileObjectState.DELETED
                        delete_keys.append(file_object.storage_key)
                await PendingProductImageRepository(session).cancel_for_session(
                    ingestion_session.id, at=current
                )
                self._audit(
                    session,
                    actor_user_id=None,
                    action="INGESTION_SESSION_EXPIRED",
                    entity_type="ingestion_session",
                    entity_id=str(ingestion_session.id),
                )
        for key in delete_keys:
            self._delete_quietly(key)
        return len(expired)

    async def _open_session(
        self, session: AsyncSession, user_id: int
    ) -> tuple[IngestionSession, bool]:
        existing = await self._existing_open_session(session, user_id, for_update=True)
        if existing is not None:
            return existing, False
        now = datetime.now(UTC)
        created = IngestionSession(
            user_id=user_id,
            status=IngestionSessionStatus.OPEN,
            expires_at=now + self.ttl,
        )
        session.add(created)
        await session.flush()
        return created, True

    async def _existing_open_session(
        self, session: AsyncSession, user_id: int, *, for_update: bool = False
    ) -> IngestionSession | None:
        statement = select(IngestionSession).where(
            IngestionSession.user_id == user_id,
            IngestionSession.status == IngestionSessionStatus.OPEN,
        )
        if for_update:
            statement = statement.with_for_update()
        ingestion_session = (await session.execute(statement)).scalar_one_or_none()
        if ingestion_session is not None and ingestion_session.expires_at <= datetime.now(UTC):
            ingestion_session.status = IngestionSessionStatus.EXPIRED
            ingestion_session = None
        return ingestion_session

    async def _active_user(self, session: AsyncSession, telegram_user_id: int) -> User:
        user = (
            await session.execute(
                select(User).where(User.telegram_user_id == telegram_user_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not is_active(user):
            raise AuthorizationDenied("Only active users may use ingestion")
        assert user is not None
        return user

    @staticmethod
    async def _file_by_sha(
        session: AsyncSession, sha256: str, *, for_update: bool = False
    ) -> FileObject | None:
        statement = select(FileObject).where(FileObject.sha256 == sha256)
        if for_update:
            statement = statement.with_for_update()
        return (await session.execute(statement)).scalar_one_or_none()

    @staticmethod
    async def _item_for_message(
        session: AsyncSession, chat_id: int, message_id: int
    ) -> IngestionSessionItem | None:
        return (
            await session.execute(
                select(IngestionSessionItem).where(
                    IngestionSessionItem.telegram_chat_id == chat_id,
                    IngestionSessionItem.telegram_message_id == message_id,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _items(session: AsyncSession, session_id: int) -> list[IngestionSessionItem]:
        return list(
            (
                await session.execute(
                    select(IngestionSessionItem)
                    .where(IngestionSessionItem.session_id == session_id)
                    .order_by(IngestionSessionItem.sequence_no)
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    async def _file_count(session: AsyncSession, session_id: int) -> int:
        return int(
            await session.scalar(
                select(func.count(IngestionSessionItem.id)).where(
                    IngestionSessionItem.session_id == session_id,
                    IngestionSessionItem.item_type == IngestionItemType.FILE,
                )
            )
            or 0
        )

    @staticmethod
    async def _next_sequence(session: AsyncSession, session_id: int) -> int:
        maximum = await session.scalar(
            select(func.max(IngestionSessionItem.sequence_no)).where(
                IngestionSessionItem.session_id == session_id
            )
        )
        return int(maximum or 0) + 1

    async def _upload_result(
        self, session: AsyncSession, session_id: int, *, duplicate: bool
    ) -> UploadResult:
        items = await self._items(session, session_id)
        return UploadResult(
            summary=SessionSummary(
                session_id=session_id,
                file_count=sum(item.item_type == IngestionItemType.FILE for item in items),
                instruction_count=sum(
                    item.item_type == IngestionItemType.INSTRUCTION for item in items
                ),
                duplicate_count=0,
            ),
            duplicate=duplicate,
        )

    @staticmethod
    def _hash(path: Path) -> tuple[str, int]:
        from office_assistant.infrastructure.storage.hashing import sha256_file

        return sha256_file(path)

    @staticmethod
    def _require_active(user: User) -> None:
        if not is_active(user):
            raise AuthorizationDenied("Only active users may use ingestion")

    @staticmethod
    def _audit(
        session: AsyncSession,
        *,
        actor_user_id: int | None,
        action: str,
        entity_type: str,
        entity_id: str,
    ) -> None:
        session.add(
            AuditEvent(
                actor_user_id=actor_user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
            )
        )

    def _delete_quietly(self, storage_key: str) -> None:
        try:
            self.storage.delete(storage_key)
        except StorageError:
            pass
