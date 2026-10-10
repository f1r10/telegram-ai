"""Persistence for a picture confirmation the ingestion session has not committed yet (§23.7)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import (
    FileObject,
    IngestionSessionItem,
    PendingProductImageConfirmation,
    PendingProductImageState,
)


@dataclass(frozen=True, slots=True)
class QueuedPicture:
    """One queued claim together with the file and message that give it content and provenance."""

    pending: PendingProductImageConfirmation
    file: FileObject
    telegram_message_id: int


class PendingProductImageRepository:
    """Works in the caller's transaction, because the queueing and the save are one story.

    A queued confirmation is read only while it is still ``PENDING``: resolved rows stay as the
    record of what was asked and what became of it, so nothing here deletes history.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def pending_for_item(
        self, session_item_id: int, *, for_update: bool = False
    ) -> PendingProductImageConfirmation | None:
        statement: Select[tuple[PendingProductImageConfirmation]] = select(
            PendingProductImageConfirmation
        ).where(
            PendingProductImageConfirmation.session_item_id == session_item_id,
            PendingProductImageConfirmation.status == PendingProductImageState.PENDING,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def pending_with_files_for_session(self, session_id: int) -> list[QueuedPicture]:
        """Every claim waiting in this session, each joined to the bytes it speaks about.

        The joins are inner ones, so a returned row always has a file: a queued claim that lost its
        upload would otherwise be promoted against nothing.
        """

        rows = (
            await self.session.execute(
                select(PendingProductImageConfirmation, FileObject, IngestionSessionItem)
                .join(
                    IngestionSessionItem,
                    IngestionSessionItem.id == PendingProductImageConfirmation.session_item_id,
                )
                .join(FileObject, FileObject.id == IngestionSessionItem.file_object_id)
                .where(
                    PendingProductImageConfirmation.session_id == session_id,
                    PendingProductImageConfirmation.status == PendingProductImageState.PENDING,
                )
                .order_by(PendingProductImageConfirmation.id)
            )
        ).all()
        return [
            QueuedPicture(
                pending=row[0], file=row[1], telegram_message_id=row[2].telegram_message_id
            )
            for row in rows
        ]

    def add(
        self,
        *,
        session_id: int,
        session_item_id: int,
        product_id: UUID,
        canonical_code: str,
    ) -> PendingProductImageConfirmation:
        pending = PendingProductImageConfirmation(
            session_id=session_id,
            session_item_id=session_item_id,
            product_id=product_id,
            canonical_code=canonical_code,
            status=PendingProductImageState.PENDING,
        )
        self.session.add(pending)
        return pending

    @staticmethod
    def retarget(
        pending: PendingProductImageConfirmation, *, product_id: UUID, canonical_code: str
    ) -> PendingProductImageConfirmation:
        """Point the same queued item at another product, before anything was committed.

        The user changed their mind in the caption, not in the database: until ``/saxla`` there is
        no authoritative relation to supersede, and one live row per message item is what the
        partial unique index asks for.
        """

        pending.product_id = product_id
        pending.canonical_code = canonical_code
        return pending

    @staticmethod
    def resolve(
        pending: PendingProductImageConfirmation, state: PendingProductImageState, *, at: datetime
    ) -> None:
        pending.status = state
        pending.resolved_at = at

    async def cancel_for_session(self, session_id: int, *, at: datetime) -> None:
        """Close every claim this session still holds, without touching committed relations."""

        await self.session.execute(
            update(PendingProductImageConfirmation)
            .where(
                PendingProductImageConfirmation.session_id == session_id,
                PendingProductImageConfirmation.status == PendingProductImageState.PENDING,
            )
            .values(status=PendingProductImageState.CANCELLED, resolved_at=at)
        )
