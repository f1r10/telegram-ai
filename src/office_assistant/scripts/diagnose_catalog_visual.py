"""Inspect staging catalog-photo search readiness without exposing file names or secrets.

Default mode only inspects effective settings and active PDF image references.
--probe-model tests local CLIP on one verified catalog image (no network access).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from hashlib import sha256
from pathlib import Path

from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.processing.image_similarity import MAX_IMAGE_BYTES
from office_assistant.processing.semantic_visual import SemanticVisualSearch
from office_assistant.scripts.prepare_catalog_visual_references import catalog_references


async def check_catalog(
    settings: Settings, *, probe_model: bool = False
) -> dict[str, str | int | bool]:
    references = await catalog_references(settings)
    result: dict[str, str | int | bool] = {
        "diagnostic": "CATALOG_VISUAL_LOCAL",
        "semantic_enabled": settings.visual_semantic_enabled,
        "configured_min_score": str(settings.visual_semantic_min_score),
        "reference_limit": settings.visual_semantic_max_references,
        "active_pdf_images": len(references),
        "reference_window_incomplete": len(references)
        > settings.visual_semantic_max_references,
    }
    if not references:
        result["status"] = "NO_INDEXED_PDF_IMAGES"
        return result
    if not settings.visual_semantic_enabled:
        result["status"] = "SEMANTIC_DISABLED"
        return result
    if not probe_model:
        result["status"] = "REFERENCES_FOUND_MODEL_NOT_PROBED"
        return result
    storage = LocalFileStorage(settings.file_storage_path)
    root = storage.root.resolve()
    test_data: bytes | None = None
    test_reference = None
    for ref in references[: settings.visual_semantic_max_references]:
        try:
            path = (root / ref.storage_key).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                continue
            if not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
                continue
            body = path.read_bytes()
            if sha256(body).hexdigest() != ref.content_sha256:
                continue
        except (OSError, ValueError):
            continue
        test_data = body
        test_reference = ref
        break
    if test_data is None or test_reference is None:
        result["status"] = "NO_VALID_PDF_IMAGE_BYTES"
        return result
    search = SemanticVisualSearch(
        storage_root=root,
        model_cache_root=Path(settings.embedding_cache_path),
        version=settings.visual_semantic_model_version,
        min_score=settings.visual_semantic_min_score,
        max_references=settings.visual_semantic_max_references,
    )
    try:
        outcome = await asyncio.wait_for(
            asyncio.to_thread(search.search, test_data, [test_reference]), timeout=60
        )
    except TimeoutError:
        result["status"] = "LOCAL_MODEL_PROBE_TIMEOUT"
        return result
    result["model_available"] = not outcome.unavailable
    result["model_inspected"] = outcome.inspected
    result["status"] = (
        "READY"
        if not outcome.unavailable and outcome.inspected > 0 and outcome.candidates
        else "MODEL_OR_EMBEDDINGS_UNAVAILABLE"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-model", action="store_true")
    arguments = parser.parse_args()
    logging.disable(logging.WARNING)
    try:
        result = asyncio.run(check_catalog(load_settings(), probe_model=arguments.probe_model))
    except Exception as error:
        print(json.dumps({"diagnostic": "CATALOG_VISUAL_LOCAL", "status": "ERROR",
                          "error_type": type(error).__name__}, ensure_ascii=False))
        raise SystemExit(2) from None
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] not in {"READY", "REFERENCES_FOUND_MODEL_NOT_PROBED"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
