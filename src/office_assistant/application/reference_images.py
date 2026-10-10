"""Queue a picture confirmation in the session that holds the file, then link it at /saxla.

Two boundaries, and this module exists to keep them apart. The naming caption ("Bu KA45-dir, yadda
saxla") is read without a model and states an *intent*: it writes no bytes and no business relation,
because /saxla is the ingestion commit boundary and §23.7 says a cancel must not leave an
authoritative relation behind. The intent therefore waits inside the session as
``pending_product_image_confirmations``. The second half runs in that session's commit transaction,
where the picture content is registered and the relation written, so the file and its product claim
become true together or not at all.

Three rules shape every branch: an unknown product is never created, a picture already confirmed for
another product is never relinked (§22.7), and a caption that named no readable product resolves
nothing. Each refusal writes nothing, and each check that could still be undone happens before the
first row.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_common import require_actor
from office_assistant.application.errors import ApplicationError
from office_assistant.business.types import ProvenanceInput
from office_assistant.infrastructure.database.models import (
    FileObject,
    ImageAsset,
    IngestionItemType,
    IngestionSession,
    IngestionSessionItem,
    IngestionSessionStatus,
    PendingProductImageState,
    ProductImageRelationConfirmation,
    ProductImageRelationRole,
    SourceType,
)
from office_assistant.infrastructure.storage.protocol import FileStorage
from office_assistant.processing.image_assets import ImageAssetRegistry
from office_assistant.repositories.audit import AuditRepository
from office_assistant.repositories.pending_product_images import PendingProductImageRepository
from office_assistant.repositories.product_image_relations import ProductImageRelationRepository
from office_assistant.repositories.products import ProductRepository
from office_assistant.repositories.provenance import ProvenanceRepository

logger = logging.getLogger(__name__)


class ReferenceImageStatus(StrEnum):
    """How the caption turn ended, in the distinctions the handler answers in Azerbaijani."""

    QUEUED = "QUEUED"
    ALREADY_LINKED = "ALREADY_LINKED"
    PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
    CONFLICT = "CONFLICT"
    NO_PICTURE = "NO_PICTURE"
    SESSION_CLOSED = "SESSION_CLOSED"


@dataclass(frozen=True, slots=True)
class ReferenceImageResult:
    """The outcome, plus the code worth naming back to the user.

    On :attr:`ReferenceImageStatus.CONFLICT` that code is the *other* product's, because a conflict
    the user cannot read is a silent failure with extra steps.
    """

    status: ReferenceImageStatus
    display_code: str | None = None


@dataclass(frozen=True, slots=True)
class MessagePicture:
    """What one captioned message actually carried, and where a claim about it may wait."""

    item: IngestionSessionItem
    file: FileObject
    session_open: bool


class ReferenceImageDenialReason(StrEnum):
    """Why a queued picture could not be linked when its session committed."""

    CONFLICT = "CONFLICT"
    UNREADABLE_PICTURE = "UNREADABLE_PICTURE"


class ReferenceImageSaveDenied(ApplicationError):
    """A queued claim could not be honored at /saxla, so the whole save was rolled back.

    Raised rather than reported because a session that stored the file and quietly dropped the
    confirmation its own caption asked for is the silent failure §23.7 forbids. The code the user
    typed — and, for a conflict, the code that already owns the picture — travel with it so the
    reply can name what happened.
    """

    def __init__(
        self,
        reason: ReferenceImageDenialReason,
        *,
        canonical_code: str,
        holder_code: str | None = None,
    ) -> None:
        super().__init__(f"{reason} for queued picture {canonical_code}")
        self.reason = reason
        self.canonical_code = canonical_code
        self.holder_code = holder_code


class ReferenceImageService:
    """Puts one product's claim on one exact picture into the session that will commit it."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: FileStorage,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage

    async def record_product_picture(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        telegram_message_id: int,
        canonical_code: str,
    ) -> ReferenceImageResult:
        """Queue the picture this message carried for the product its caption named.

        ``canonical_code`` is the normalized code from
        :func:`office_assistant.business.reference_confirmation.parse_reference_confirmation`, so no
        caller repeats the normalization and nothing here re-reads prose. An actor who may not write
        raises :class:`AuthorizationDenied`; a database failure raises for the caller's error net.
        """

        async with self.session_factory.begin() as session:
            await require_actor(session, telegram_user_id)
            product = await ProductRepository(session).find_by_code(canonical_code)
            if product is None:
                # §23.7: an unknown product is never created, and nothing else is attempted.
                logger.info(
                    "Reference image declined | code=%s reason=product_not_found", canonical_code
                )
                return ReferenceImageResult(ReferenceImageStatus.PRODUCT_NOT_FOUND)
            picture = await self._message_picture(session, telegram_chat_id, telegram_message_id)
            if picture is None:
                logger.info(
                    "Reference image declined | code=%s reason=no_picture_in_message",
                    canonical_code,
                )
                return ReferenceImageResult(ReferenceImageStatus.NO_PICTURE)
            if not picture.session_open:
                # The item exists but its session already committed or cancelled, so no open
                # package is left for this claim to wait inside.
                logger.info(
                    "Reference image declined | code=%s reason=session_not_open", canonical_code
                )
                return ReferenceImageResult(ReferenceImageStatus.SESSION_CLOSED)
            item, file = picture.item, picture.file
            holder = await ProductImageRelationRepository(
                session
            ).active_confirmation_holder_by_content(file.sha256)
            if holder is not None:
                if holder[0] == product.id:
                    return ReferenceImageResult(
                        ReferenceImageStatus.ALREADY_LINKED, display_code=product.display_code
                    )
                logger.info(
                    "Reference image declined | code=%s reason=already_confirmed_elsewhere",
                    canonical_code,
                )
                return ReferenceImageResult(ReferenceImageStatus.CONFLICT, display_code=holder[1])
            pendings = PendingProductImageRepository(session)
            existing = await pendings.pending_for_item(item.id, for_update=True)
            if existing is None:
                pendings.add(
                    session_id=item.session_id,
                    session_item_id=item.id,
                    product_id=product.id,
                    canonical_code=canonical_code,
                )
            else:
                if existing.canonical_code != canonical_code:
                    logger.info(
                        "Reference image requeued | previous_code=%s code=%s",
                        existing.canonical_code,
                        canonical_code,
                    )
                pendings.retarget(existing, product_id=product.id, canonical_code=canonical_code)
            logger.info(
                "Reference image queued | code=%s product_id=%s session_id=%s",
                canonical_code,
                product.id,
                item.session_id,
            )
            return ReferenceImageResult(
                ReferenceImageStatus.QUEUED, display_code=product.display_code
            )

    @staticmethod
    async def _message_picture(
        session: AsyncSession, telegram_chat_id: int, telegram_message_id: int
    ) -> MessagePicture | None:
        """The file this exact message carried, plus whether its session can still hold a claim.

        The caption and the picture are one message, so the claim is located by that message rather
        than by the newest upload in the session, which could be a different file. The open flag
        travels too: a queued claim needs a live session to wait in, and a closed one must not be
        reported as a missing picture.
        """

        row = (
            await session.execute(
                select(IngestionSessionItem, FileObject, IngestionSession.status)
                .join(FileObject, FileObject.id == IngestionSessionItem.file_object_id)
                .join(IngestionSession, IngestionSession.id == IngestionSessionItem.session_id)
                .where(
                    IngestionSessionItem.telegram_chat_id == telegram_chat_id,
                    IngestionSessionItem.telegram_message_id == telegram_message_id,
                    IngestionSessionItem.item_type == IngestionItemType.FILE,
                )
                .with_for_update(of=IngestionSessionItem)
            )
        ).first()
        if row is None:
            return None
        return MessagePicture(
            item=row[0],
            file=row[1],
            session_open=row[2] == IngestionSessionStatus.OPEN,
        )


async def promote_queued_confirmations(
    session: AsyncSession,
    storage: FileStorage,
    *,
    session_id: int,
    actor_user_id: int,
) -> int:
    """Link every picture this session's captions claimed, inside the session's own commit.

    Called with the caller's transaction so the file activation and its product relation reach the
    database together; a refusal raises and takes the commit down with it. The content copy is
    content-addressed, so a rollback that already stored bytes leaves no duplicate behind — the next
    registration of the same picture reuses the same key.
    """

    pending_images = PendingProductImageRepository(session)
    pendings = await pending_images.pending_with_files_for_session(session_id)
    if not pendings:
        return 0
    relations = ProductImageRelationRepository(session)
    # One confirmation at a time, so a second session naming the same bytes meets a committed fact
    # instead of a race (§22.7).
    await relations.lock_confirmation_namespace()
    registry = ImageAssetRegistry(session, storage)
    now = datetime.now(UTC)
    linked = 0
    for queued in pendings:
        pending, file = queued.pending, queued.file
        asset_id = await session.scalar(
            select(ImageAsset.id).where(ImageAsset.content_sha256 == file.sha256)
        )
        if asset_id is None:
            data = await asyncio.to_thread(storage.path_for_key(file.storage_key).read_bytes)
            asset_id = await registry.ensure_content(data)
            if asset_id is None:
                # Bytes that state no picture cannot be confirmed as one; a row with guessed content
                # would be trusted later as a reference it never earned (§23.5).
                raise ReferenceImageSaveDenied(
                    ReferenceImageDenialReason.UNREADABLE_PICTURE,
                    canonical_code=pending.canonical_code,
                )
        holder = await relations.active_confirmation_holder_by_content(file.sha256)
        if holder is not None and holder[0] != pending.product_id:
            # Named for another product between the caption and the save: the existing claim stands
            # and nothing here is relinked silently (§22.7).
            raise ReferenceImageSaveDenied(
                ReferenceImageDenialReason.CONFLICT,
                canonical_code=pending.canonical_code,
                holder_code=holder[1],
            )
        if holder is None:
            source = ProvenanceRepository(session).add(
                ProvenanceInput(
                    source_type=SourceType.TELEGRAM_USER,
                    file_id=file.id,
                    ingestion_session_item_id=pending.session_item_id,
                    locator={"telegram_message_id": queued.telegram_message_id},
                    description="The user named this product code in the picture caption",
                ),
                actor_user_id=actor_user_id,
            )
            await session.flush()
            relation = relations.add(
                product_id=pending.product_id,
                image_asset_id=asset_id,
                role=ProductImageRelationRole.USER_CONFIRMED,
                is_reference=True,
                confirmation_type=ProductImageRelationConfirmation.USER_EXPLICIT,
                source_reference_id=source.id,
                created_by_user_id=actor_user_id,
            )
            await session.flush()
            AuditRepository(session).add(
                actor_user_id=actor_user_id,
                action="PRODUCT_IMAGE_CONFIRMED",
                entity_type="product_image_relation",
                entity_id=str(relation.id),
                metadata={
                    "product_id": str(pending.product_id),
                    "image_asset_id": str(asset_id),
                    "code": pending.canonical_code,
                    "ingestion_session_id": str(session_id),
                },
            )
        PendingProductImageRepository.resolve(pending, PendingProductImageState.LINKED, at=now)
        logger.info(
            "Reference image confirmed | code=%s product_id=%s asset_id=%s",
            pending.canonical_code,
            pending.product_id,
            asset_id,
        )
        linked += 1
    return linked
