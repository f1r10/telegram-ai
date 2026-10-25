"""Prewarm approved SKU and embedded-PDF image vectors on the private host.

All embedding inference runs offline using an already installed model.
Query photos are never stored. Only counts are printed; this is not
authorization to identify a PDF illustration as an approved catalog SKU.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    FileObject,
    FileObjectState,
    ImageAsset,
    ImageAssetOccurrence,
    ImageAssetOrigin,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.processing.semantic_visual import SemanticReference, SemanticVisualSearch
from office_assistant.scripts.prepare_visual_references import approved_references


@dataclass(frozen=True, slots=True)
class CatalogWarmSummary:
    approved: int
    pdf: int
    prepared: int
    skipped: int
    truncated: bool
    unavailable: bool


async def pdf_image_references(settings: Settings, *, limit: int) -> list[SemanticReference]:
    """Use only indexed image assets belonging to currently active PDF files."""

    if not 1 <= limit <= 500:
        raise ValueError("PDF reference limit must be from 1 to 500")
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    select(
                        ImageAssetOccurrence.id,
                        ImageAsset.storage_key,
                        ImageAsset.content_sha256,
                    )
                    .select_from(ImageAssetOccurrence)
                    .join(ImageAsset, ImageAsset.id == ImageAssetOccurrence.image_asset_id)
                    .join(FileObject, FileObject.id == ImageAssetOccurrence.source_file_id)
                    .where(
                        ImageAssetOccurrence.origin_type == ImageAssetOrigin.PDF_EMBEDDED,
                        FileObject.state == FileObjectState.ACTIVE,
                    )
                    .order_by(ImageAssetOccurrence.id.desc())
                    .limit(limit + 1)
                )
            ).all()
        return [SemanticReference(str(id_), key, digest) for id_, key, digest in rows]
    finally:
        await engine.dispose()


async def prepare_catalog(
    settings: Settings, *, apply: bool = False, limit: int = 500
) -> CatalogWarmSummary:
    """Dry-run counts coverage; --apply verifies bytes and caches vectors offline."""

    if not 1 <= limit <= 500:
        raise ValueError("Catalog image limit must be from 1 to 500")
    approved = await approved_references(settings, limit=limit)
    pdf = await pdf_image_references(settings, limit=limit)
    truncated = len(approved) > limit or len(pdf) > limit
    selected_approved = approved[:limit]
    selected_pdf = pdf[:limit]
    if not apply:
        return CatalogWarmSummary(
            len(selected_approved), len(selected_pdf), 0, 0, truncated, False
        )

    storage = LocalFileStorage(settings.file_storage_path)
    service = SemanticVisualSearch(
        storage_root=storage.root,
        model_cache_root=Path(settings.embedding_cache_path),
        version=settings.visual_semantic_model_version,
        min_score=settings.visual_semantic_min_score,
        max_references=limit,
    )
    first = await asyncio.to_thread(service.warm_references, selected_approved)
    if first.unavailable:
        return CatalogWarmSummary(
            len(selected_approved),
            len(selected_pdf),
            first.warmed,
            first.skipped,
            truncated or first.incomplete,
            True,
        )
    second = await asyncio.to_thread(service.warm_references, selected_pdf)
    return CatalogWarmSummary(
        len(selected_approved),
        len(selected_pdf),
        first.warmed + second.warmed,
        first.skipped + second.skipped,
        truncated or first.incomplete or second.incomplete,
        second.unavailable,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Use offline model and cache vectors")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    try:
        result = asyncio.run(
            prepare_catalog(load_settings(), apply=args.apply, limit=args.limit)
        )
    except Exception as error:
        print(f"CATALOG_VISUAL_PREWARM=FAILED error_type={type(error).__name__}")
        raise SystemExit(1) from None
    status = "UNAVAILABLE" if result.unavailable else "APPLIED" if args.apply else "DRY_RUN"
    print(
        f"CATALOG_VISUAL_PREWARM={status} approved={result.approved} "
        f"pdf={result.pdf} prepared={result.prepared} skipped={result.skipped} "
        f"truncated={str(result.truncated).lower()}"
    )
    if result.unavailable:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
