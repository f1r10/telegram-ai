"""Prepare offline CLIP vectors for pictures embedded in active PDF catalogs.

Dry-run is read only. --download-model explicitly provisions an open model into
the local volume; no model download ever occurs on a Telegram message.
--apply caches only validated PDF image references, not incoming query photos.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
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
from office_assistant.processing.semantic_visual import (
    CLIP_MODEL,
    SemanticReference,
    SemanticVisualSearch,
)

MAX_CATALOG_REFERENCES = 500


async def catalog_references(settings: Settings) -> list[SemanticReference]:
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
                    .limit(MAX_CATALOG_REFERENCES + 1)
                )
            ).all()
            return [SemanticReference(str(oid), key, digest) for oid, key, digest in rows]
    finally:
        await engine.dispose()


def _provision_model(cache: Path) -> None:
    """Explicit operator-only network action, NEVER called on user query."""

    from fastembed import ImageEmbedding

    ImageEmbedding(
        model_name=CLIP_MODEL,
        cache_dir=str(cache),
        local_files_only=False,
        threads=2,
    )


async def prepare_catalog(
    settings: Settings, *, apply: bool, limit: int
) -> tuple[int, int, int, bool, bool]:
    if not 1 <= limit <= MAX_CATALOG_REFERENCES:
        raise ValueError("Catalog visual limit must be between 1 and 500")
    rows = await catalog_references(settings)
    count = len(rows)
    selected = rows[:limit]
    incomplete = count > len(selected)
    if not apply:
        return count, len(selected), 0, incomplete, False
    storage = LocalFileStorage(settings.file_storage_path)
    search = SemanticVisualSearch(
        storage_root=storage.root,
        model_cache_root=Path(settings.embedding_cache_path),
        version=settings.visual_semantic_model_version,
        min_score=settings.visual_semantic_min_score,
        max_references=limit,
    )
    warmed = await asyncio.to_thread(search.warm_references, selected)
    return (
        count,
        warmed.inspected,
        warmed.warmed,
        incomplete or warmed.incomplete,
        warmed.unavailable,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Compute cached PDF image vectors")
    parser.add_argument(
        "--download-model",
        action="store_true",
        help="Operator opt-in to download local open image model weights",
    )
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    settings = load_settings()
    if args.download_model:
        try:
            _provision_model(Path(settings.embedding_cache_path))
        except Exception as error:
            print(f"CATALOG_MODEL_STATUS=UNAVAILABLE error_type={type(error).__name__}")
            raise SystemExit(2) from None
        print("CATALOG_MODEL_STATUS=PROVISIONED")
    try:
        found, selected, warmed, incomplete, unavailable = asyncio.run(
            prepare_catalog(settings, apply=args.apply, limit=args.limit)
        )
    except Exception as error:
        print(f"CATALOG_VISUAL_PREWARM_STATUS=FAILED error_type={type(error).__name__}")
        raise SystemExit(1) from None
    status = "UNAVAILABLE" if unavailable else "APPLIED" if args.apply else "DRY_RUN"
    print(f"CATALOG_VISUAL_PREWARM_STATUS={status}")
    print(
        f"eligible={found} inspected={selected} warmed={warmed} "
        f"incomplete={str(incomplete).lower()}"
    )
    if unavailable or (args.apply and warmed == 0 and selected > 0):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
