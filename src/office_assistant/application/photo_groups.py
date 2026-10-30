"""Persistent photo group metadata and member links over committed original files.

The source file, provenance and any user-confirmed product reference remain
untouched. A group is not an authoritative catalog item or business fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.errors import AuthorizationDenied, InvalidStateTransition
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    FileObject,
    FileObjectState,
    IngestionItemType,
    IngestionSessionItem,
    PhotoGroup,
    PhotoGroupImage,
    User,
    UserStatus,
)

MAX_GROUP_PHOTOS = 48
MAX_DESCRIPTION_CHARS = 3000


@dataclass(frozen=True, slots=True)
class PhotoGroupView:
    id: int
    description: str
    image_count: int
    revision: int

    @property
    def title(self) -> str:
        first = next(
            (line.partition(":")[2].strip() for line in self.description.splitlines()
             if line.casefold().startswith("ad:")),
            "Şəkil qrupu",
        )
        return first[:100]


def validated_description(description: str) -> str:
    text = description.strip()
    if not 5 <= len(text) <= MAX_DESCRIPTION_CHARS:
        raise ValueError("Photo group metadata must be 5-3000 characters")
    if re.search(r"(?im)^ad\s*:\s*\S.{0,180}$", text) is None:
        raise ValueError("Photo group requires an Ad: title")
    return text


async def apply_photo_group_commit(
    session: AsyncSession,
    *,
    actor_id: int,
    session_id: int,
    file_items: list[IngestionSessionItem],
    new_description: str | None = None,
    append_group_id: int | None = None,
) -> int | None:
    """Update group membership inside the existing /saxla transaction."""

    if new_description is None and append_group_id is None:
        return None
    if new_description is not None and append_group_id is not None:
        raise InvalidStateTransition("Cannot create and append photo group together")
    if not 1 <= len(file_items) <= 12:
        raise InvalidStateTransition("Photo group commit requires 1-12 files")
    hashes: list[str] = []
    for item in file_items:
        if item.item_type != IngestionItemType.FILE or item.file_object_id is None:
            raise InvalidStateTransition("Photo group can only contain committed image files")
        source = await session.get(FileObject, item.file_object_id)
        if (
            source is None
            or source.state != FileObjectState.ACTIVE
            or source.content_type not in {"image/jpeg", "image/png"}
        ):
            raise InvalidStateTransition("Photo group source is not an active supported image")
        hashes.append(source.sha256)
    if len(set(hashes)) != len(hashes):
        raise InvalidStateTransition("Photo group includes duplicate image content")

    if new_description is not None:
        group = PhotoGroup(
            owner_user_id=actor_id,
            source_session_id=session_id,
            metadata_text=validated_description(new_description),
            revision=1,
        )
        session.add(group)
        await session.flush()
        start = 0
        action = "PHOTO_GROUP_CREATED"
    else:
        group = (
            await session.execute(
                select(PhotoGroup)
                .where(
                    PhotoGroup.id == append_group_id,
                    PhotoGroup.owner_user_id == actor_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if group is None:
            raise AuthorizationDenied("Photo group does not belong to this actor")
        existing = (
            await session.execute(
                select(FileObject.sha256)
                .select_from(PhotoGroupImage)
                .join(
                    IngestionSessionItem,
                    IngestionSessionItem.id == PhotoGroupImage.session_item_id,
                )
                .join(FileObject, FileObject.id == IngestionSessionItem.file_object_id)
                .where(PhotoGroupImage.group_id == group.id)
            )
        ).scalars().all()
        if any(digest in existing for digest in hashes):
            raise InvalidStateTransition("An identical photo already belongs to this group")
        start = int(
            await session.scalar(
                select(func.coalesce(func.max(PhotoGroupImage.position), 0))
                .where(PhotoGroupImage.group_id == group.id)
            ) or 0
        )
        action = "PHOTO_GROUP_PHOTOS_APPENDED"
    if start + len(file_items) > MAX_GROUP_PHOTOS:
        raise InvalidStateTransition("Photo group is at its image limit")
    for index, item in enumerate(file_items, start=start + 1):
        session.add(
            PhotoGroupImage(group_id=group.id, session_item_id=item.id, position=index)
        )
    group.updated_at = datetime.now(UTC)
    session.add(
        AuditEvent(
            actor_user_id=actor_id,
            action=action,
            entity_type="photo_group",
            entity_id=str(group.id),
            event_metadata={"image_count_added": len(file_items)},
        )
    )
    await session.flush()
    return group.id


class PhotoGroupService:
    """Only the active creator may list/edit/append a group's source links."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    @staticmethod
    async def _actor(session: AsyncSession, telegram_user_id: int) -> int:
        user = (
            await session.execute(
                select(User).where(
                    User.telegram_user_id == telegram_user_id,
                    User.status == UserStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()
        if user is None:
            raise AuthorizationDenied("Active user required for photo group")
        return user.id

    async def get(self, telegram_user_id: int, group_id: int) -> PhotoGroupView | None:
        async with self.session_factory() as session:
            actor_id = await self._actor(session, telegram_user_id)
            row = (
                await session.execute(
                    select(PhotoGroup, func.count(PhotoGroupImage.id))
                    .outerjoin(PhotoGroupImage, PhotoGroup.id == PhotoGroupImage.group_id)
                    .where(
                        PhotoGroup.id == group_id,
                        PhotoGroup.owner_user_id == actor_id,
                    )
                    .group_by(PhotoGroup.id)
                )
            ).one_or_none()
            if row is None:
                return None
            group, count = row
            return PhotoGroupView(group.id, group.metadata_text, int(count), group.revision)

    async def list_own(self, telegram_user_id: int) -> list[PhotoGroupView]:
        async with self.session_factory() as session:
            actor_id = await self._actor(session, telegram_user_id)
            rows = (
                await session.execute(
                    select(PhotoGroup, func.count(PhotoGroupImage.id))
                    .outerjoin(PhotoGroupImage, PhotoGroup.id == PhotoGroupImage.group_id)
                    .where(PhotoGroup.owner_user_id == actor_id)
                    .group_by(PhotoGroup.id)
                    .order_by(PhotoGroup.id.desc())
                    .limit(30)
                )
            ).all()
            return [
                PhotoGroupView(group.id, group.metadata_text, int(count), group.revision)
                for group, count in rows
            ]

    async def update(
        self, telegram_user_id: int, group_id: int, description: str
    ) -> PhotoGroupView:
        metadata = validated_description(description)
        async with self.session_factory.begin() as session:
            actor_id = await self._actor(session, telegram_user_id)
            group = (
                await session.execute(
                    select(PhotoGroup)
                    .where(PhotoGroup.id == group_id, PhotoGroup.owner_user_id == actor_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if group is None:
                raise AuthorizationDenied("Photo group does not belong to actor")
            group.metadata_text = metadata
            group.revision += 1
            group.updated_at = datetime.now(UTC)
            count = int(
                await session.scalar(
                    select(func.count(PhotoGroupImage.id)).where(
                        PhotoGroupImage.group_id == group.id
                    )
                ) or 0
            )
            session.add(
                AuditEvent(
                    actor_user_id=actor_id,
                    action="PHOTO_GROUP_METADATA_UPDATED",
                    entity_type="photo_group",
                    entity_id=str(group.id),
                    event_metadata={"revision": group.revision},
                )
            )
            return PhotoGroupView(group.id, group.metadata_text, count, group.revision)
