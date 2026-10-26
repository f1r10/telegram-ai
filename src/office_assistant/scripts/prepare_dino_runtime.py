"""Operator-only DINOv2 CPU model preparation and synthetic offline inference probe.

Default: local_files_only (never downloads). --download-model is an explicit
one-time opt-in to fetch public model weights into the private model-cache
volume, NEVER used on Telegram requests. No company image is read or uploaded.
"""

from __future__ import annotations

import argparse
import logging
import tempfile
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from time import perf_counter
from typing import Protocol, cast

from PIL import Image

from office_assistant.core.settings import load_settings
from office_assistant.processing.semantic_visual import DINO_DIMENSION, unit_vector
from office_assistant.processing.visual_dinov2 import MODEL_ID, LocalDinoMeanEncoder


class _Factory(Protocol):
    def from_pretrained(
        self, model_id: str, *, cache_dir: str, local_files_only: bool
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class DinoProbe:
    dimension: int
    inference_ms: int


def download_dino_weights(cache_root: Path) -> None:
    """Explicit operator action; no user-supplied inputs or query photos."""

    transformers = import_module("transformers")
    processor = cast(_Factory, transformers.AutoImageProcessor)
    model = cast(_Factory, transformers.AutoModel)
    for factory in (processor, model):
        factory.from_pretrained(MODEL_ID, cache_dir=str(cache_root), local_files_only=False)


def probe_dino(cache_root: Path, *, download_model: bool = False) -> DinoProbe:
    """Read only an artificial probe; fail if local weights/dimensions are wrong."""

    if download_model:
        download_dino_weights(cache_root)
    with tempfile.TemporaryDirectory(prefix="office-dino-probe-") as directory:
        path = Path(directory) / "synthetic.png"
        Image.new("RGB", (224, 224), color=(80, 90, 110)).save(path)
        start = perf_counter()
        vectors = LocalDinoMeanEncoder(cache_root).embed([path])
        elapsed = round((perf_counter() - start) * 1000)
    if len(vectors) != 1 or unit_vector(vectors[0], dimension=DINO_DIMENSION) is None:
        raise ValueError("DINOv2 returned an invalid embedding")
    return DinoProbe(DINO_DIMENSION, elapsed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download-model",
        action="store_true",
        help="Explicitly download public DINOv2 weights for the staging cache",
    )
    options = parser.parse_args()
    logging.disable(logging.WARNING)
    try:
        root = Path(load_settings().embedding_cache_path).resolve()
        result = probe_dino(root, download_model=options.download_model)
    except Exception as error:
        print(f"DINO_RUNTIME=UNAVAILABLE error_type={type(error).__name__}")
        raise SystemExit(2) from None
    print(f"DINO_RUNTIME=READY dimension={result.dimension} inference_ms={result.inference_ms}")
    print("DINO_PROBE=ONLY_SYNTHETIC_IMAGE no_catalog_or_query_photo_read=true")


if __name__ == "__main__":
    main()
