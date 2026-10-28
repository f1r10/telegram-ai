"""DINOv2 384-dimensional Qdrant index for unbounded source corpora.

Only bounded top-K points are read per query; PostgreSQL ACTIVE/explicitly
confirmed records and on-disk SHA bytes are rechecked before every result.
The image ANN collection is separate from the existing document RAG index.
"""

from __future__ import annotations

# Qdrant's generated client types are dynamic; narrow results at this boundary.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
import asyncio
import logging
from dataclasses import dataclass
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from office_assistant.infrastructure.database.models import (
    FileObject,
    FileObjectState,
    ImageAsset,
    ImageAssetOccurrence,
    ImageAssetOrigin,
    IngestionItemType,
    IngestionSession,
    IngestionSessionItem,
    Product,
    ProductImageRelation,
    ProductImageRelationConfirmation,
    SystemMetadata,
)
from office_assistant.processing.document_visual_candidates import (
    DOCUMENT_IMAGE_ORIGINS,
    DocumentImageSource,
)
from office_assistant.processing.semantic_visual import (
    DINO_DIMENSION,
    DINO_MODEL_VERSION,
    SemanticVisualSearch,
)

logger = logging.getLogger(__name__)
INDEX_NAME = "office_visual_dino_v1"
MAX_HITS = 40
MAX_SCAN_HITS = 120  # overfetch to survive revoked/stale high-score points
MIN_CANDIDATE_SCORE = 0.82


def point_id(kind: str, record_id: str | int | UUID) -> str:
    if kind not in {"product", "document"}:
        raise ValueError("Invalid index kind")
    return str(uuid5(NAMESPACE_URL, f"office:visual:dino:{kind}:{record_id}"))


@dataclass(frozen=True, slots=True)
class IndexedVisualPoint:
    kind: str
    record_id: str
    digest: str
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class IndexedProduct:
    code: str
    score: float


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    source: DocumentImageSource
    score: float


@dataclass(frozen=True, slots=True)
class IndexedResults:
    products: tuple[IndexedProduct, ...]
    documents: tuple[IndexedDocument, ...]
    unavailable: bool = False


class VisualANNIndex:
    """A separate 384-d cosine projection; zero coupling to the RAG collection."""

    def __init__(self, url: str, *, collection: str = INDEX_NAME) -> None:
        if not collection.startswith("office_visual_"):
            raise ValueError("Visual index collection must use its own namespace")
        self.collection = collection
        self.client = AsyncQdrantClient(url=url, timeout=12)

    async def close(self) -> None:
        await self.client.close()

    async def ensure(self) -> None:
        try:
            await self.client.get_collection(self.collection)
        except Exception as error:
            if getattr(error, "status_code", None) != 404:
                raise
            await self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=DINO_DIMENSION, distance=models.Distance.COSINE
                ),
                on_disk_payload=True,
            )
            await self.client.create_payload_index(
                collection_name=self.collection,
                field_name="kind",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    async def upsert(self, items: list[IndexedVisualPoint]) -> int:
        points = []
        for item in items:
            if (
                item.kind not in {"product", "document"}
                or len(item.vector) != DINO_DIMENSION
                or len(item.digest) != 64
            ):
                continue
            points.append(
                models.PointStruct(
                    id=point_id(item.kind, item.record_id),
                    vector=list(item.vector),
                    payload={
                        "kind": item.kind,
                        "record_id": item.record_id,
                        "digest": item.digest,
                        "version": DINO_MODEL_VERSION,
                    },
                )
            )
        if points:
            await self.client.upsert(
                collection_name=self.collection,
                points=points,
                wait=True,
            )
        return len(points)

    async def query(
        self, vector: tuple[float, ...], *, limit: int = MAX_HITS
    ) -> list[tuple[str, str, str, float]]:
        if len(vector) != DINO_DIMENSION or not 1 <= limit <= MAX_SCAN_HITS:
            raise ValueError("Invalid visual index query")
        response = await self.client.query_points(
            collection_name=self.collection,
            query=list(vector),
            limit=limit,
            with_payload=True,
        )
        output: list[tuple[str, str, str, float]] = []
        for scored in response.points:
            payload = scored.payload or {}
            kind = payload.get("kind")
            rid = payload.get("record_id")
            digest = payload.get("digest")
            version = payload.get("version")
            if (
                kind in {"product", "document"}
                and isinstance(rid, str)
                and isinstance(digest, str)
                and version == DINO_MODEL_VERSION
            ):
                output.append((kind, rid, digest, float(scored.score)))
        return output

    async def prune_stale(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        batch_size: int = 128,
    ) -> tuple[int, int]:
        """Reconcile ONLY derived Qdrant points with authoritative active DB rows.

        Called after a successful full backfill, not in user photo requests.
        New committed worker points are retained; revoked or retired points are
        deleted. Raw originals and business records are never changed.
        """
        if not 1 <= batch_size <= 256:
            raise ValueError("Invalid reconciliation batch size")
        offset: models.ExtendedPointId | None = None
        scanned = removed = 0
        while True:
            records, next_offset = await self.client.scroll(
                collection_name=self.collection,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            product_ids: list[UUID] = []
            document_ids: list[int] = []
            parsed: list[tuple[str, str, str, str]] = []
            for point in records:
                payload = point.payload or {}
                kind = payload.get("kind")
                rid = payload.get("record_id")
                digest = payload.get("digest")
                version = payload.get("version")
                point_uuid = str(point.id)
                if (
                    not isinstance(kind, str)
                    or not isinstance(rid, str)
                    or not isinstance(digest, str)
                ):
                    parsed.append((point_uuid, "", "", ""))
                    continue
                if version != DINO_MODEL_VERSION:
                    parsed.append((point_uuid, "", "", ""))
                    continue
                if kind == "product":
                    try:
                        product_ids.append(UUID(rid))
                    except ValueError:
                        parsed.append((point_uuid, "", "", ""))
                        continue
                elif kind == "document":
                    try:
                        occurrence = int(rid)
                        if occurrence <= 0:
                            raise ValueError
                        document_ids.append(occurrence)
                    except ValueError:
                        parsed.append((point_uuid, "", "", ""))
                        continue
                else:
                    parsed.append((point_uuid, "", "", ""))
                    continue
                parsed.append((point_uuid, kind, rid, digest))
            active: set[tuple[str, str, str]] = set()
            async with factory() as session:
                if product_ids:
                    rows = (
                        await session.execute(
                            select(ProductImageRelation.id, ImageAsset.content_sha256)
                            .select_from(ProductImageRelation)
                            .join(Product, Product.id == ProductImageRelation.product_id)
                            .join(ImageAsset, ImageAsset.id == ProductImageRelation.image_asset_id)
                            .where(
                                ProductImageRelation.id.in_(product_ids),
                                ProductImageRelation.confirmation_type
                                == ProductImageRelationConfirmation.USER_EXPLICIT,
                                ProductImageRelation.superseded_at.is_(None),
                                Product.is_active.is_(True),
                            )
                        )
                    ).all()
                    active.update(("product", str(rid), digest) for rid, digest in rows)
                if document_ids:
                    rows = (
                        await session.execute(
                            select(ImageAssetOccurrence.id, ImageAsset.content_sha256)
                            .select_from(ImageAssetOccurrence)
                            .join(ImageAsset, ImageAsset.id == ImageAssetOccurrence.image_asset_id)
                            .join(FileObject, FileObject.id == ImageAssetOccurrence.source_file_id)
                            .where(
                                ImageAssetOccurrence.id.in_(document_ids),
                                ImageAssetOccurrence.origin_type.in_(DOCUMENT_IMAGE_ORIGINS),
                                FileObject.state == FileObjectState.ACTIVE,
                            )
                        )
                    ).all()
                    active.update(("document", str(rid), digest) for rid, digest in rows)
            stale = [
                point_id_value
                for point_id_value, kind, rid, digest in parsed
                if (kind, rid, digest) not in active
            ]
            scanned += len(records)
            if stale:
                await self.client.delete(
                    collection_name=self.collection,
                    points_selector=models.PointIdsList(
                        points=[cast(models.ExtendedPointId, value) for value in stale]
                    ),
                    wait=True,
                )
                removed += len(stale)
            if next_offset is None:
                break
            offset = cast(models.ExtendedPointId | None, next_offset)
        return scanned, removed


class IndexedVisualLookup:
    """Validate ranked ANN candidates against current PostgreSQL and source bytes."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        index: VisualANNIndex,
        encoder: SemanticVisualSearch,
    ) -> None:
        self.factory = factory
        self.index = index
        self.encoder = encoder

    async def search(self, body: bytes) -> IndexedResults:
        if self.encoder.version != DINO_MODEL_VERSION:
            return IndexedResults((), (), unavailable=True)
        # Never claim an incomplete first-batch index covers the entire archive.
        async with self.factory() as session:
            ready = await session.scalar(
                select(SystemMetadata.value).where(
                    SystemMetadata.key == "office_visual_dino_ann_ready_v1"
                )
            )
        if ready != "ready":
            return IndexedResults((), (), unavailable=True)
        try:
            vector = await asyncio.to_thread(self.encoder.query_vector, body)
            if vector is None:
                return IndexedResults((), ())
            points = await self.index.query(vector, limit=MAX_SCAN_HITS)
        except Exception as error:  # noqa: BLE001 - remote ANN must fail closed
            logger.warning("Visual ANN unavailable | type=%s", type(error).__name__)
            return IndexedResults((), (), unavailable=True)
        products: dict[str, IndexedProduct] = {}
        documents: list[IndexedDocument] = []
        async with self.factory() as session:
            for kind, rid, digest, score in points:
                if len(products) + len(documents) >= MAX_HITS:
                    break
                if score < MIN_CANDIDATE_SCORE:
                    continue
                if kind == "product":
                    try:
                        relation_id = UUID(rid)
                    except ValueError:
                        continue
                    row = (
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
                                ProductImageRelation.id == relation_id,
                                ProductImageRelation.confirmation_type
                                == ProductImageRelationConfirmation.USER_EXPLICIT,
                                ProductImageRelation.superseded_at.is_(None),
                                Product.is_active.is_(True),
                            )
                        )
                    ).first()
                    if row is None:
                        continue
                    code, key, sha = row
                    if sha != digest or not self.encoder.verify_source(key, sha):
                        continue
                    if code not in products:
                        products[code] = IndexedProduct(code, score)
                else:
                    try:
                        occurrence_id = int(rid)
                    except ValueError:
                        continue
                    row = (
                        await session.execute(
                            select(
                                ImageAssetOccurrence.source_file_id,
                                FileObject.original_filename,
                                ImageAssetOccurrence.origin_type,
                                ImageAssetOccurrence.locator,
                                ImageAsset.storage_key,
                                ImageAsset.content_sha256,
                            )
                            .select_from(ImageAssetOccurrence)
                            .join(ImageAsset, ImageAsset.id == ImageAssetOccurrence.image_asset_id)
                            .join(FileObject, FileObject.id == ImageAssetOccurrence.source_file_id)
                            .where(
                                ImageAssetOccurrence.id == occurrence_id,
                                ImageAssetOccurrence.origin_type.in_(DOCUMENT_IMAGE_ORIGINS),
                                FileObject.state == FileObjectState.ACTIVE,
                            )
                        )
                    ).first()
                    if row is None:
                        continue
                    file_id, name, origin, locator, key, sha = row
                    if sha != digest or not self.encoder.verify_source(key, sha):
                        continue
                    description = ""
                    if origin == ImageAssetOrigin.STANDALONE_UPLOAD:
                        file_item = aliased(IngestionSessionItem)
                        instruction = aliased(IngestionSessionItem)
                        description_row = await session.scalar(
                            select(instruction.text_content)
                            .select_from(file_item)
                            .join(
                                IngestionSession,
                                IngestionSession.id == file_item.session_id,
                            )
                            .join(
                                instruction,
                                instruction.session_id == file_item.session_id,
                            )
                            .where(
                                file_item.file_object_id == file_id,
                                IngestionSession.status == "COMMITTED",
                                instruction.item_type == IngestionItemType.INSTRUCTION,
                                instruction.text_content.like("BİR ŞƏKİL QRUPU (%"),
                            )
                            .order_by(instruction.id.desc())
                            .limit(1)
                        )
                        if isinstance(description_row, str):
                            description = description_row.split("):", 1)[-1].strip()[:600]
                    source = DocumentImageSource(
                        occurrence_id, file_id, name, origin, locator, key, sha, description
                    )
                    documents.append(IndexedDocument(source, score))
        return IndexedResults(
            tuple(sorted(products.values(), key=lambda item: -item.score)),
            tuple(documents),
        )
