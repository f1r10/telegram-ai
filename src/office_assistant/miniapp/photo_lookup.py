"""Read-only, independently authorized visual lookup for mobile Mini App photos.

Matches only confirmed active catalog image references, never creates a
business identity, sale, inventory balance, upload, or knowledge document.
Similarity candidates always require a human to check the product code.
"""

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.infrastructure.database.models import (
    ImageAsset,
    Product,
    ProductImageRelation,
    ProductImageRelationConfirmation,
)
from office_assistant.processing.image_metadata import image_signature
from office_assistant.processing.image_similarity import (
    MAX_IMAGE_BYTES,
    VisualCandidate,
    fingerprint,
    shortlist,
)
from office_assistant.processing.semantic_visual import SemanticReference, SemanticVisualSearch
from office_assistant.repositories.product_image_relations import ProductImageRelationRepository

MAX_LOOKUP_BYTES = min(4 * 1024 * 1024, MAX_IMAGE_BYTES)
MAX_REFERENCES = 500
_WARNING = (
    "Oxşarlıq məhsulun eyniliyini, qiymətini və anbarda olmasını təsdiqləmir. Kodu ayrıca yoxlayın."
)


class InvalidLookupPhoto(ValueError):
    """The bytes are not a safe, usable, textured product photograph."""


@dataclass(frozen=True, slots=True)
class PhotoLookupResult:
    status: str
    codes: tuple[str, ...]
    method: str
    incomplete: bool
    notice: str

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "codes": list(self.codes),
            "method": self.method,
            "incomplete": self.incomplete,
            "notice": self.notice,
        }


class MiniAppPhotoLookup:
    """No persisted query photo, no model calls unless explicitly opted in."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        storage_root: Path,
        semantic: SemanticVisualSearch | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.storage_root = storage_root
        self.semantic = semantic

    async def lookup(self, photo: bytes) -> PhotoLookupResult:
        if not 0 < len(photo) <= MAX_LOOKUP_BYTES:
            raise InvalidLookupPhoto("Photo exceeds bounded request size")
        # Decode the supplied image BEFORE reporting a SHA as product identity:
        # arbitrary uploaded binary data must never match a historical digest.
        if image_signature(photo) is None or await asyncio.to_thread(fingerprint, photo) is None:
            raise InvalidLookupPhoto("A valid non-uniform photograph is required")

        digest = sha256(photo).hexdigest()
        async with self.session_factory() as session:
            holder = await ProductImageRelationRepository(
                session
            ).active_confirmation_holder_by_content(digest, active_product_only=True)
            if holder is not None:
                return PhotoLookupResult(
                    "exact",
                    (holder[1],),
                    "confirmed_sha256",
                    False,
                    "Bu şəkil faylı aktiv məhsula təsdiqlənmiş nümunə ilə eynidir. "
                    "Kataloq uyğunluğu anbar qalığını sübut etmir.",
                )
            existing = await session.scalar(
                select(ImageAsset.id).where(ImageAsset.content_sha256 == digest)
            )
            rows = (
                await session.execute(
                    select(
                        Product.display_code,
                        ImageAsset.storage_key,
                        ImageAsset.content_sha256,
                    )
                    .select_from(ProductImageRelation)
                    .join(Product, Product.id == ProductImageRelation.product_id)
                    .join(ImageAsset, ImageAsset.id == ProductImageRelation.image_asset_id)
                    .where(
                        ProductImageRelation.confirmation_type
                        == ProductImageRelationConfirmation.USER_EXPLICIT,
                        ProductImageRelation.superseded_at.is_(None),
                        Product.is_active.is_(True),
                    )
                    .order_by(ProductImageRelation.created_at.desc(), ProductImageRelation.id)
                    .limit(MAX_REFERENCES + 1)
                )
            ).all()

        incomplete = len(rows) > MAX_REFERENCES
        approved = rows[:MAX_REFERENCES]
        references = [
            VisualCandidate(code, key, content_sha256=digest) for code, key, digest in approved
        ]
        near = await asyncio.to_thread(
            shortlist, photo, references, self.storage_root, max_results=3
        )
        if near:
            return PhotoLookupResult(
                "candidates",
                tuple(x.product_code for x in near),
                "near_duplicate",
                incomplete,
                _WARNING,
            )
        if approved and self.semantic is not None:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.semantic.search,
                        photo,
                        [SemanticReference(code, key, sha) for code, key, sha in approved],
                    ),
                    timeout=5.0,
                )
            except TimeoutError:
                return PhotoLookupResult(
                    "unavailable",
                    (),
                    "offline_model",
                    incomplete,
                    "Şəkil modeli cavab vermədi. Məhsulu kodla axtarın.",
                )
            if result.unavailable:
                return PhotoLookupResult(
                    "unavailable",
                    (),
                    "offline_model",
                    incomplete or result.incomplete,
                    "Şəkil modeli hazırda əlçatan deyil. Məhsulu kodla axtarın.",
                )
            if result.candidates:
                return PhotoLookupResult(
                    "candidates",
                    tuple(x.product_code for x in result.candidates),
                    "cross_angle",
                    incomplete or result.incomplete,
                    _WARNING,
                )
            incomplete = incomplete or result.incomplete

        if existing is not None:
            return PhotoLookupResult(
                "unconfirmed",
                (),
                "no_active_confirmation",
                incomplete,
                "Şəkil bazadadır, lakin aktiv məhsula təsdiqlənmiş əlaqəsi yoxdur.",
            )
        return PhotoLookupResult(
            "not_found",
            (),
            "no_match",
            incomplete,
            "Təsdiqli fotolar arasında uyğun nümunə tapılmadı. "
            "Bu, məhsulun kataloqda olmadığı demək deyil.",
        )
