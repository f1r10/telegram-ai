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
from office_assistant.processing.document_visual_candidates import (
    DocumentImageHit,
    active_document_images,
    document_image_location,
    find_document_images,
)
from office_assistant.processing.image_metadata import image_signature
from office_assistant.processing.image_similarity import (
    MAX_IMAGE_BYTES,
    VisualCandidate,
    fingerprint,
    shortlist,
)
from office_assistant.processing.semantic_visual import SemanticReference, SemanticVisualSearch
from office_assistant.processing.visual_ann import (
    IndexedResults,
    IndexedVisualLookup,
)
from office_assistant.repositories.product_image_relations import ProductImageRelationRepository

MAX_LOOKUP_BYTES = min(4 * 1024 * 1024, MAX_IMAGE_BYTES)
MAX_REFERENCES = 500
_WARNING = (
    "Oxşarlıq məhsulun eyniliyini, qiymətini və anbarda olmasını təsdiqləmir. Kodu ayrıca yoxlayın."
)


def ranked_ann_candidates(indexed: IndexedResults) -> tuple[dict[str, str | float], ...]:
    """One score-ordered, strictly source-checked candidate list for Mini App.

    The index only supplies candidates after current PostgreSQL/SHA verification.
    Never convert a cosine score into a confidence percentage or a product fact.
    """
    items: list[tuple[float, dict[str, str | float]]] = [
        (product.score, {"kind": "product", "code": product.code, "score": product.score})
        for product in indexed.products
    ]
    for item in indexed.documents:
        source = item.source
        entry: dict[str, str | float] = {
            "kind": "source",
            "occurrence_id": str(source.occurrence_id),
            "filename": " ".join(source.filename.split())[:96],
            "type": source.origin.replace("_EMBEDDED", ""),
            "location": document_image_location(source),
            "score": item.score,
        }
        if source.description:
            entry["description"] = source.description[:320]
        items.append((item.score, entry))
    items.sort(key=lambda row: -row[0])
    return tuple(item for _, item in items[:40])


class InvalidLookupPhoto(ValueError):
    """The bytes are not a safe, usable, textured product photograph."""


@dataclass(frozen=True, slots=True)
class PhotoLookupResult:
    status: str
    codes: tuple[str, ...]
    method: str
    incomplete: bool
    notice: str
    documents: tuple[DocumentImageHit, ...] = ()
    ranked: tuple[dict[str, str | float], ...] = ()

    @property
    def match_tier(self) -> str | None:
        """Evidence tier, not an AI confidence percentage or product identity."""
        if self.status == "exact" and self.method == "confirmed_sha256":
            return "EXACT_MATCH"
        if self.status == "candidates" and self.method == "near_duplicate":
            return "PROBABLE_MATCH"
        if self.status in {"candidates", "source_candidates"}:
            return "SIMILAR"
        if self.status in {"not_found", "unconfirmed"}:
            return "NO_MATCH"
        return None  # Provider failure is not evidence of absence.

    def payload(self) -> dict[str, object]:
        result: dict[str, object] = {
            "status": self.status,
            "match_tier": self.match_tier,
            "codes": list(self.codes),
            "method": self.method,
            "incomplete": self.incomplete,
            "notice": self.notice,
        }
        if self.ranked:
            # Never mutate reusable result objects while replacing private
            # occurrence IDs with actor-specific short-lived source tickets.
            result["ranked"] = [dict(candidate) for candidate in self.ranked]
        if self.documents:
            result["sources"] = [
                {
                    "filename": " ".join(hit.source.filename.split())[:96],
                    "type": hit.source.origin.replace("_EMBEDDED", ""),
                    "location": document_image_location(hit.source),
                    **(
                        {"description": hit.source.description[:320]}
                        if hit.source.description
                        else {}
                    ),
                    "method": hit.match_type,
                }
                for hit in self.documents
            ]
        return result


class MiniAppPhotoLookup:
    """No persisted query photo, no model calls unless explicitly opted in."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        storage_root: Path,
        semantic: SemanticVisualSearch | None = None,
        indexed: IndexedVisualLookup | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.storage_root = storage_root
        self.semantic = semantic
        self.indexed = indexed

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

        if self.indexed is not None:
            indexed = await self.indexed.search(photo)
            if not indexed.unavailable:
                if indexed.products or indexed.documents:
                    hits = tuple(
                        DocumentImageHit(item.source, "semantic", item.score)
                        for item in indexed.documents[:40]
                    )
                    return PhotoLookupResult(
                        "candidates" if indexed.products else "source_candidates",
                        tuple(item.code for item in indexed.products[:3]),
                        "dino_qdrant_all_active",
                        False,
                        _WARNING,
                        hits,
                        ranked_ann_candidates(indexed),
                    )
                return PhotoLookupResult(
                    "not_found",
                    (),
                    "dino_qdrant_no_candidate",
                    False,
                    "Bütün aktiv vizual indeks üzrə güclü namizəd tapılmadı.",
                )

        async with self.session_factory() as session:
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
        model_failed = False
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
                model_failed = True
            else:
                if result.unavailable:
                    model_failed = True
                elif result.candidates:
                    return PhotoLookupResult(
                        "candidates",
                        tuple(x.product_code for x in result.candidates),
                        "cross_angle",
                        incomplete or result.incomplete,
                        _WARNING,
                    )
                incomplete = incomplete or result.incomplete

        # Documents are evidence, never product codes. Do not insert occurrence
        # IDs into the SKU list or mistake a DOCX/XLSX picture for stock.
        async with self.session_factory() as session:
            documents, document_incomplete = await active_document_images(session)
        if documents:
            try:
                hits, limited, _unavailable = await asyncio.wait_for(
                    asyncio.to_thread(
                        find_document_images,
                        photo,
                        documents,
                        self.storage_root,
                        semantic=self.semantic,
                    ),
                    timeout=5.0,
                )
            except TimeoutError:
                # A timed-out document scan did not inspect all evidence.
                # Never present its empty partial result as a completed search.
                hits = ()
                limited = True
            if hits:
                return PhotoLookupResult(
                    "source_candidates",
                    (),
                    "embedded_document",
                    incomplete or document_incomplete or limited,
                    "Şəkil aktiv PDF, DOCX və ya XLSX faylında tapılmış ola bilər. "
                    "Mənbə təsdiqlənmiş məhsul kodu, qiymət və ya stok deyil.",
                    hits,
                )
            incomplete = incomplete or document_incomplete or limited

        if model_failed:
            return PhotoLookupResult(
                "unavailable",
                (),
                "offline_model",
                incomplete,
                "Şəkil modeli hazırda əlçatan deyil. Məhsulu kodla axtarın.",
            )

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
