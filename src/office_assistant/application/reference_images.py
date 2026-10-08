"""Link a user's picture to the product they named, reading no model to do it (Master Spec §23.7).

The point of this service is that an explicit human statement is the strongest provenance the
system has (§22.3), so it is recorded exactly as stated and never inferred. Three rules follow
straight from the spec and shape every branch below: an unknown product is not created, a picture
already confirmed for another product is not relinked (§22.7), and a caption that named no
readable product resolves nothing. Each is a refusal that writes nothing, and every check that
could still be undone happens before the first copy of bytes or the first row.
"""

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_common import require_actor
from office_assistant.business.types import ProvenanceInput
from office_assistant.infrastructure.database.models import (
    FileObject,
    ImageAsset,
    IngestionItemType,
    IngestionSessionItem,
    ProductImageRelationConfirmation,
    ProductImageRelationRole,
    SourceType,
)
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.processing.image_assets import ImageAssetRegistry
from office_assistant.repositories.audit import AuditRepository
from office_assistant.repositories.product_image_relations import ProductImageRelationRepository
from office_assistant.repositories.products import ProductRepository
from office_assistant.repositories.provenance import ProvenanceRepository

logger = logging.getLogger(__name__)


class ReferenceImageStatus(StrEnum):
    """How the confirmation ended, in the distinctions the handler answers in Azerbaijani."""

    LINKED = "LINKED"
    ALREADY_LINKED = "ALREADY_LINKED"
    PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
    CONFLICT = "CONFLICT"
    NO_PICTURE = "NO_PICTURE"
    UNREADABLE_PICTURE = "UNREADABLE_PICTURE"


@dataclass(frozen=True, slots=True)
class ReferenceImageResult:
    """The outcome, plus the code worth naming back to the user.

    On :attr:`ReferenceImageStatus.CONFLICT` that code is the *other* product's, because a conflict
    the user cannot read is a silent failure with extra steps.
    """

    status: ReferenceImageStatus
    display_code: str | None = None
    image_asset_id: UUID | None = None


class ReferenceImageService:
    """Records ``Bu KA45-dir, yadda saxla`` as one product's claim on one exact picture."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage

    async def confirm_product_picture(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        telegram_message_id: int,
        canonical_code: str,
    ) -> ReferenceImageResult:
        """Link the picture this message carried to the product its caption named.

        ``canonical_code`` is the normalized code from
        :func:`office_assistant.business.reference_confirmation.parse_reference_confirmation`, so no
        caller repeats the normalization and nothing here re-reads prose. An actor who may not write
        raises :class:`AuthorizationDenied`; a database failure raises for the caller's error net.
        """

        async with self.session_factory.begin() as session:
            actor = await require_actor(session, telegram_user_id)
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
            item, file = picture
            relations = ProductImageRelationRepository(session)
            # One confirmation at a time, so a second product naming the same bytes meets a
            # committed fact instead of a race (§22.7).
            await relations.lock_confirmation_namespace()
            asset_id = await session.scalar(
                select(ImageAsset.id).where(ImageAsset.content_sha256 == file.sha256)
            )
            if asset_id is None:
                data = await asyncio.to_thread(
                    self.storage.path_for_key(file.storage_key).read_bytes
                )
                asset_id = await ImageAssetRegistry(session, self.storage).ensure_content(data)
                if asset_id is None:
                    # Bytes that state no picture cannot be confirmed as one; a row with guessed
                    # content would be trusted later as a reference it never earned (§23.5).
                    logger.info(
                        "Reference image declined | file_id=%s reason=unreadable_picture", file.id
                    )
                    return ReferenceImageResult(ReferenceImageStatus.UNREADABLE_PICTURE)
            holder = await relations.active_confirmation_holder(asset_id)
            if holder is not None:
                if holder[0] == product.id:
                    return ReferenceImageResult(
                        ReferenceImageStatus.ALREADY_LINKED,
                        display_code=product.display_code,
                        image_asset_id=asset_id,
                    )
                logger.info(
                    "Reference image declined | code=%s reason=already_confirmed_elsewhere",
                    canonical_code,
                )
                return ReferenceImageResult(
                    ReferenceImageStatus.CONFLICT,
                    display_code=holder[1],
                    image_asset_id=asset_id,
                )
            source = ProvenanceRepository(session).add(
                ProvenanceInput(
                    source_type=SourceType.TELEGRAM_USER,
                    file_id=file.id,
                    ingestion_session_item_id=item.id,
                    locator={"telegram_message_id": telegram_message_id},
                    description="The user named this product code in the picture caption",
                ),
                actor_user_id=actor.id,
            )
            await session.flush()
            relation = relations.add(
                product_id=product.id,
                image_asset_id=asset_id,
                role=ProductImageRelationRole.USER_CONFIRMED,
                is_reference=True,
                confirmation_type=ProductImageRelationConfirmation.USER_EXPLICIT,
                source_reference_id=source.id,
                created_by_user_id=actor.id,
            )
            await session.flush()
            AuditRepository(session).add(
                actor_user_id=actor.id,
                action="PRODUCT_IMAGE_CONFIRMED",
                entity_type="product_image_relation",
                entity_id=str(relation.id),
                metadata={
                    "product_id": str(product.id),
                    "image_asset_id": str(asset_id),
                    "code": canonical_code,
                },
            )
            logger.info(
                "Reference image confirmed | code=%s product_id=%s", canonical_code, product.id
            )
            return ReferenceImageResult(
                ReferenceImageStatus.LINKED,
                display_code=product.display_code,
                image_asset_id=asset_id,
            )

    @staticmethod
    async def _message_picture(
        session: AsyncSession, telegram_chat_id: int, telegram_message_id: int
    ) -> tuple[IngestionSessionItem, FileObject] | None:
        """The file this exact message carried, or nothing.

        The caption and the picture are one message, so the confirmation is located by that message
        rather than by the newest upload in the session, which could be a different file.
        """

        item = (
            await session.execute(
                select(IngestionSessionItem)
                .where(
                    IngestionSessionItem.telegram_chat_id == telegram_chat_id,
                    IngestionSessionItem.telegram_message_id == telegram_message_id,
                    IngestionSessionItem.item_type == IngestionItemType.FILE,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if item is None or item.file_object_id is None:
            return None
        file = await session.get(FileObject, item.file_object_id)
        return None if file is None else (item, file)
