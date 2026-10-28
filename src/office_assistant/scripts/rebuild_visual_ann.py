"""Explicit offline DINOv2 visual ANN backfill for ALL approved and document images.

Safe default is dry-run. --apply iterates keyset pages until PostgreSQL is
exhausted (no 500 or 10,000 global cap). The index is a derived projection;
SKU/stock/authoritative document state remain in PostgreSQL.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    FileObject,
    FileObjectState,
    ImageAsset,
    ImageAssetOccurrence,
    Product,
    ProductImageRelation,
    ProductImageRelationConfirmation,
    SystemMetadata,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.processing.document_visual_candidates import DOCUMENT_IMAGE_ORIGINS
from office_assistant.processing.semantic_visual import (
    DINO_MODEL_VERSION,
    SemanticReference,
    SemanticVisualSearch,
)
from office_assistant.processing.visual_ann import IndexedVisualPoint, VisualANNIndex

INDEX_READY_KEY = "office_visual_dino_ann_ready_v1"
BATCH = 64


async def _ready(session: AsyncSession, value: str) -> None:
    await session.execute(
        insert(SystemMetadata)
        .values(key=INDEX_READY_KEY, value=value)
        .on_conflict_do_update(
            index_elements=[SystemMetadata.key],
            set_={"value": value},
        )
    )


async def rebuild(
    factory: async_sessionmaker[AsyncSession],
    encoder: SemanticVisualSearch,
    index: VisualANNIndex,
    *,
    apply: bool,
    batch_size: int = BATCH,
) -> tuple[int, int, int]:
    """Full keyset scan; all active product relations and document occurrences."""

    if not 1 <= batch_size <= 256 or encoder.version != DINO_MODEL_VERSION:
        raise ValueError("DINO ANN backfill requires valid model and batch size")
    if apply:
        async with factory.begin() as session:
            await _ready(session, "building")
        await index.ensure()

    totals = [0, 0, 0]
    for kind in ("product", "document"):
        cursor: UUID | int | None = None
        while True:
            async with factory() as session:
                if kind == "product":
                    query = (
                        select(
                            ProductImageRelation.id,
                            ImageAsset.storage_key,
                            ImageAsset.content_sha256,
                        )
                        .select_from(ProductImageRelation)
                        .join(ImageAsset, ImageAsset.id == ProductImageRelation.image_asset_id)
                        .join(Product, Product.id == ProductImageRelation.product_id)
                        .where(
                            ProductImageRelation.confirmation_type
                            == ProductImageRelationConfirmation.USER_EXPLICIT,
                            ProductImageRelation.superseded_at.is_(None),
                            Product.is_active.is_(True),
                        )
                        .order_by(ProductImageRelation.id)
                        .limit(batch_size)
                    )
                    if isinstance(cursor, UUID):
                        query = query.where(ProductImageRelation.id > cursor)
                else:
                    query = (
                        select(
                            ImageAssetOccurrence.id,
                            ImageAsset.storage_key,
                            ImageAsset.content_sha256,
                        )
                        .select_from(ImageAssetOccurrence)
                        .join(ImageAsset, ImageAsset.id == ImageAssetOccurrence.image_asset_id)
                        .join(FileObject, FileObject.id == ImageAssetOccurrence.source_file_id)
                        .where(
                            ImageAssetOccurrence.origin_type.in_(DOCUMENT_IMAGE_ORIGINS),
                            FileObject.state == FileObjectState.ACTIVE,
                        )
                        .order_by(ImageAssetOccurrence.id)
                        .limit(batch_size)
                    )
                    if isinstance(cursor, int):
                        query = query.where(ImageAssetOccurrence.id > cursor)
                rows = (await session.execute(query)).all()
            if not rows:
                break
            cursor = cast(UUID | int, rows[-1][0])
            points: list[IndexedVisualPoint] = []
            for rid, key, digest in rows:
                totals[0 if kind == "product" else 1] += 1
                if not apply:
                    continue
                reference = SemanticReference(str(rid), key, digest)
                vector = await asyncio.to_thread(encoder.source_vector, reference)
                if vector is None:
                    totals[2] += 1
                    continue
                points.append(IndexedVisualPoint(kind, str(rid), digest, vector))
            if apply and points:
                await index.upsert(points)
            if len(rows) < batch_size:
                break
    if apply and totals[2] == 0:
        # A previous ANN snapshot can contain revoked/superseded points.
        # Never mark full coverage READY until derived stale points are gone.
        inspected, pruned = await index.prune_stale(factory)
        logging.info(
            "Visual ANN reconciliation | inspected=%s pruned=%s", inspected, pruned
        )
        async with factory.begin() as session:
            await _ready(session, "ready")
    return totals[0], totals[1], totals[2]


async def main_async(*, apply: bool, batch_size: int) -> None:
    settings: Settings = load_settings()
    if settings.qdrant_url is None:
        raise RuntimeError("QDRANT_URL is required for visual ANN")
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    storage = LocalFileStorage(settings.file_storage_path)
    encoder = SemanticVisualSearch(
        storage_root=storage.root,
        model_cache_root=Path(settings.embedding_cache_path),
        version=DINO_MODEL_VERSION,
    )
    index = VisualANNIndex(settings.qdrant_url)
    try:
        totals = await rebuild(factory, encoder, index, apply=apply, batch_size=batch_size)
        print(
            f"VISUAL_ANN={'APPLIED' if apply else 'DRY_RUN'} "
            f"product={totals[0]} documents={totals[1]} skipped={totals[2]} "
            f"complete={str(apply and totals[2] == 0).lower()}"
        )
        if apply and totals[2]:
            raise SystemExit(2)
    finally:
        await index.close()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=BATCH)
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    try:
        asyncio.run(main_async(apply=args.apply, batch_size=args.batch_size))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"VISUAL_ANN=UNAVAILABLE error_type={type(error).__name__}")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
