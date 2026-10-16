"""Prepare local semantic vectors from active, explicitly confirmed product photos.

Dry-run by default. Opt-in --apply performs local CPU inference with a model
already provisioned on disk; it never downloads model weights or stores user queries.
"""

import argparse
import asyncio
import logging
from pathlib import Path
from dataclasses import dataclass

from sqlalchemy import select

from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    ImageAsset,
    Product,
    ProductImageRelation,
    ProductImageRelationConfirmation,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.processing.semantic_visual import (
    ReferenceWarmResult,
    SemanticReference,
    SemanticVisualSearch,
)


@dataclass(frozen=True, slots=True)
class WarmSummary:
    inspected: int
    eligible: int
    prepared: int
    skipped: int
    truncated: bool
    unavailable: bool


async def approved_references(settings: Settings, *, limit: int) -> list[SemanticReference]:
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
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
                    .order_by(
                        ProductImageRelation.created_at.desc(),
                        ProductImageRelation.id,
                    )
                    .limit(limit + 1)
                )
            ).all()
            return [
                SemanticReference(code, storage_key, digest)
                for code, storage_key, digest in rows
            ]
    finally:
        await engine.dispose()


async def prepare_references(
    settings: Settings, *, apply: bool = False, limit: int = 80
) -> WarmSummary:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be from 1 to 500")
    references = await approved_references(settings, limit=limit)
    selected = references[:limit]
    truncated = len(references) > len(selected)
    if not apply:
        return WarmSummary(
            inspected=len(selected),
            eligible=len(selected),
            prepared=0,
            skipped=0,
            truncated=truncated,
            unavailable=False,
        )
    storage = LocalFileStorage(settings.file_storage_path)
    service = SemanticVisualSearch(
        storage_root=storage.root,
        model_cache_root=Path(settings.embedding_cache_path),
        version=settings.visual_semantic_model_version,
        min_score=settings.visual_semantic_min_score,
        max_references=limit,
    )
    outcome: ReferenceWarmResult = await asyncio.to_thread(
        service.warm_references, selected
    )
    return WarmSummary(
        inspected=outcome.inspected,
        eligible=len(selected),
        prepared=outcome.warmed,
        skipped=outcome.skipped,
        truncated=truncated or outcome.incomplete,
        unavailable=outcome.unavailable,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Perform local model inference")
    parser.add_argument("--limit", type=int, default=80)
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    try:
        summary = asyncio.run(
            prepare_references(load_settings(), apply=args.apply, limit=args.limit)
        )
    except Exception as error:
        print(f"VISUAL_PREWARM_STATUS=FAILED error_type={type(error).__name__}")
        raise SystemExit(1) from None
    print(
        "VISUAL_PREWARM_STATUS="
        + ("MODEL_UNAVAILABLE" if summary.unavailable else "APPLIED" if args.apply else "DRY_RUN")
    )
    print(
        f"inspected={summary.inspected} eligible={summary.eligible} "
        f"prepared={summary.prepared} skipped={summary.skipped} "
        f"truncated={str(summary.truncated).lower()}"
    )
    if summary.unavailable:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
