"""Image asset registration: one row per distinct content, one row per sighting (Master Spec §23.4).

Content and provenance are separate tables on purpose. A logo that appears fifty times in one
document is one set of bytes and one ``image_assets`` row, but fifty ``image_asset_occurrences``
rows, each carrying the locator that says where it was seen. Merging the two would either duplicate
bytes or lose the places, and §23.5 forbids inventing locator precision to make a merge tidy.
"""

import json
import logging
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import (
    ImageAsset,
    ImageAssetOccurrence,
    ImageAssetOrigin,
    ImageAssetStorageKind,
)
from office_assistant.infrastructure.storage.protocol import FileStorage
from office_assistant.processing.image_metadata import ImageSignature, image_signature

logger = logging.getLogger(__name__)

_CONTENT_CONSTRAINT = "uq_image_assets_content_sha256"
_OCCURRENCE_CONSTRAINT = "uq_image_asset_occurrence"
_SUFFIXES = {"JPEG": ".jpg", "PNG": ".png"}


@dataclass(frozen=True, slots=True)
class ImageAssetRegistration:
    """Outcome of one registration: which content, and whether either half was already known."""

    asset_id: UUID
    content_sha256: str
    reused_content: bool
    recorded_occurrence: bool


class ImageAssetRegistry:
    """Works in the caller's transaction so bytes and bookkeeping commit together."""

    def __init__(self, session: AsyncSession, storage: FileStorage) -> None:
        self.session = session
        self.storage = storage

    async def register(
        self,
        path: Path,
        *,
        origin: ImageAssetOrigin,
        source_file_id: int,
        source_parse_run_id: int | None = None,
        locator: dict[str, Any] | None = None,
    ) -> ImageAssetRegistration | None:
        """Register the picture held in ``path``; its bytes are read once, here."""

        return await self.register_bytes(
            path.read_bytes(),
            origin=origin,
            source_file_id=source_file_id,
            source_parse_run_id=source_parse_run_id,
            locator=locator,
        )

    async def register_bytes(
        self,
        data: bytes,
        *,
        origin: ImageAssetOrigin,
        source_file_id: int,
        source_parse_run_id: int | None = None,
        locator: dict[str, Any] | None = None,
    ) -> ImageAssetRegistration | None:
        """Register one image's bytes and this sighting of them, or report nothing registerable.

        An unsupported or unreadable header returns ``None`` rather than storing a row with guessed
        dimensions: an asset that cannot say what it is cannot later be trusted to be one. Embedded
        payloads arrive straight from a document, so their bytes never become an upload (§23.3).
        """

        signature = image_signature(data)
        if signature is None:
            logger.info(
                "Image asset skipped | file_id=%s origin=%s reason=unsupported_signature",
                source_file_id,
                origin,
            )
            return None
        content_sha256 = sha256(data).hexdigest()
        storage_key = self.storage.store_image_asset(
            data, content_sha256, _SUFFIXES[signature.format]
        )
        asset_id, reused_content = await self._register_content(
            content_sha256=content_sha256,
            signature=signature,
            byte_size=len(data),
            storage_key=storage_key,
        )
        recorded_occurrence = await self._record_occurrence(
            asset_id=asset_id,
            origin=origin,
            source_file_id=source_file_id,
            source_parse_run_id=source_parse_run_id,
            locator=locator or {},
        )
        logger.info(
            "Image asset registered | file_id=%s origin=%s reused_content=%s recorded=%s",
            source_file_id,
            origin,
            reused_content,
            recorded_occurrence,
        )
        return ImageAssetRegistration(
            asset_id=asset_id,
            content_sha256=content_sha256,
            reused_content=reused_content,
            recorded_occurrence=recorded_occurrence,
        )

    async def _register_content(
        self,
        *,
        content_sha256: str,
        signature: ImageSignature,
        byte_size: int,
        storage_key: str,
    ) -> tuple[UUID, bool]:
        statement = (
            insert(ImageAsset)
            .values(
                content_sha256=content_sha256,
                mime_type=signature.mime_type,
                format=signature.format,
                width=signature.width,
                height=signature.height,
                byte_size=byte_size,
                storage_kind=ImageAssetStorageKind.LOCAL,
                storage_key=storage_key,
            )
            .on_conflict_do_nothing(constraint=_CONTENT_CONSTRAINT)
            .returning(ImageAsset.id)
        )
        asset_id = (await self.session.execute(statement)).scalar_one_or_none()
        if asset_id is not None:
            return asset_id, False
        existing = await self.session.scalar(
            select(ImageAsset.id).where(ImageAsset.content_sha256 == content_sha256)
        )
        if existing is None:
            raise RuntimeError("Image asset disappeared during registration")
        return existing, True

    async def _record_occurrence(
        self,
        *,
        asset_id: UUID,
        origin: ImageAssetOrigin,
        source_file_id: int,
        source_parse_run_id: int | None,
        locator: dict[str, Any],
    ) -> bool:
        statement = (
            insert(ImageAssetOccurrence)
            .values(
                image_asset_id=asset_id,
                origin_type=origin,
                source_file_id=source_file_id,
                source_parse_run_id=source_parse_run_id,
                locator=locator,
                locator_sha256=locator_sha256(locator),
            )
            .on_conflict_do_nothing(constraint=_OCCURRENCE_CONSTRAINT)
            .returning(ImageAssetOccurrence.id)
        )
        recorded_id = (await self.session.execute(statement)).scalar_one_or_none()
        return recorded_id is not None


def locator_sha256(locator: dict[str, Any]) -> str:
    """Hash a canonical rendering of the locator so reprocessing a run cannot inflate provenance.

    The locator itself stays readable and is stored exactly as the parser saw it (§23.5); the hash
    exists only to make the uniqueness check indexable.
    """

    canonical = json.dumps(locator, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()
