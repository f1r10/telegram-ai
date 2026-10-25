"""Conservative, offline product-type agreement for PDF photo suggestions.

A CLIP image-image nearest neighbour is NOT evidence that two objects have the
same function (e.g. security camera vs solar-powered push button). Compare both
photos independently against a fixed, broad image/text product-type vocabulary
in the *same* CLIP joint vector space. Abstain if classification is ambiguous
or the local text encoder is absent. This is an additional safety gate, never
a product identity confirmation. No query image, text or vector is persisted.
"""

from __future__ import annotations

import tempfile
import threading
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

# Shared image preparation has a deliberately private implementation.
# pyright: reportPrivateUsage=false

from office_assistant.processing.image_similarity import MAX_IMAGE_BYTES
from office_assistant.processing.semantic_visual import (
    CLIP_DIMENSION,
    LocalClipEncoder,
    _thumbnail,
    unit_vector,
)

CLIP_TEXT_MODEL = "Qdrant/clip-ViT-B-32-text"

# Broad functional types: not brands, SKUs or catalog-derived assertions.
# These English descriptions are used by the local CLIP zero-shot encoder.
_TYPES: tuple[tuple[str, str], ...] = (
    ("security_camera", "a security surveillance CCTV camera"),
    ("wet_floor_sign", "a yellow folding caution wet floor warning sign"),
    ("electric_button", "a solar powered push button switch"),
    ("traffic_cone", "a red traffic safety cone"),
    ("road_speed_bump", "a yellow and black road speed bump"),
    ("parking_barrier", "a retractable parking barrier"),
    ("caution_tape", "a roll of red and white warning barrier tape"),
    ("traffic_bollard", "a traffic bollard post"),
    ("traffic_delineator", "a reflective traffic delineator"),
    ("solar_stud", "a solar powered reflective road stud"),
    ("solar_lamp", "a solar powered outdoor lamp"),
    ("parking_sign", "a parking road traffic information sign"),
    ("chain", "a plastic road safety chain"),
    ("safety_mirror", "a convex traffic safety mirror"),
    ("rubber_ramp", "a rubber wheelchair threshold ramp"),
    ("solar_panel", "a standalone black photovoltaic solar panel"),
    ("electric_cable", "a reel of electrical power cable"),
    ("rubber_wheel_stop", "a rubber parking wheel stop"),
    ("traffic_fence", "a folding fence barrier"),
    ("reflective_vest", "a reflective worker safety vest"),
)
# No calibrated probabilities are inferred from CLIP cosine values.
_MIN_TOP_TEXT_SCORE = 0.17
_MIN_TEXT_CATEGORY_GAP = 0.012


class VectorTextEncoder(Protocol):
    def embed(self, descriptions: list[str]) -> list[list[float]]: ...


class LocalClipTextEncoder:
    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root
        self._model: object | None = None

    def embed(self, descriptions: list[str]) -> list[list[float]]:
        from fastembed import TextEmbedding

        if self._model is None:
            self._model = TextEmbedding(
                model_name=CLIP_TEXT_MODEL,
                cache_dir=str(self.cache_root),
                local_files_only=True,
                threads=2,
            )
        model = cast(TextEmbedding, self._model)
        return [
            [float(value) for value in vector]
            for vector in model.embed(descriptions)
        ]


@dataclass(frozen=True, slots=True)
class TypeAssessment:
    type_name: str | None
    score: float
    margin: float


def classify_photo_vector(
    image_vector: list[float],
    text_vectors: list[list[float]],
) -> TypeAssessment:
    """A weak/ambiguous prediction has no accepted product-type label."""

    normal = unit_vector(image_vector)
    if normal is None or len(text_vectors) != len(_TYPES):
        return TypeAssessment(None, 0.0, 0.0)
    scores: list[tuple[float, str]] = []
    for (type_name, _), vector in zip(_TYPES, text_vectors, strict=True):
        normalized = unit_vector(vector)
        if normalized is None:
            return TypeAssessment(None, 0.0, 0.0)
        scores.append(
            (sum(left * right for left, right in zip(normal, normalized, strict=True)), type_name)
        )
    scores.sort(reverse=True)
    winner, type_name = scores[0]
    margin = winner - scores[1][0]
    if winner < _MIN_TOP_TEXT_SCORE or margin < _MIN_TEXT_CATEGORY_GAP:
        return TypeAssessment(None, winner, margin)
    return TypeAssessment(type_name, winner, margin)


class LocalCatalogCategoryVerifier:
    """One model context per app worker, with bounded serialized inference."""

    def __init__(
        self,
        model_root: Path,
        *,
        image_encoder: LocalClipEncoder | None = None,
        text_encoder: VectorTextEncoder | None = None,
    ) -> None:
        self.image_encoder = image_encoder or LocalClipEncoder(model_root)
        self.text_encoder = text_encoder or LocalClipTextEncoder(model_root)
        self._text_vectors: list[list[float]] | None = None
        self._lock = threading.Lock()

    def agree(self, query: bytes, reference: bytes) -> bool | None:
        """True=same type; False=different types; None=unverifiable, fail closed."""

        if (
            not query
            or not reference
            or len(query) > MAX_IMAGE_BYTES
            or len(reference) > MAX_IMAGE_BYTES
        ):
            return None
        if not self._lock.acquire(blocking=False):
            return None
        try:
            with tempfile.TemporaryDirectory(prefix="office-category-") as directory:
                input_path = Path(directory) / "query.png"
                ref_path = Path(directory) / "reference.png"
                if not _thumbnail(query, input_path) or not _thumbnail(reference, ref_path):
                    return None
                if self._text_vectors is None:
                    self._text_vectors = self.text_encoder.embed(
                        [description for _, description in _TYPES]
                    )
                image_vectors = self.image_encoder.embed([input_path, ref_path])
                if len(image_vectors) != 2:
                    return None
                query_type = classify_photo_vector(image_vectors[0], self._text_vectors)
                reference_type = classify_photo_vector(image_vectors[1], self._text_vectors)
                if query_type.type_name is None or reference_type.type_name is None:
                    return None
                return query_type.type_name == reference_type.type_name
        except (OSError, ValueError, RuntimeError, ImportError):
            return None
        finally:
            self._lock.release()


@lru_cache(maxsize=3)
def local_category_verifier(cache_root: str) -> LocalCatalogCategoryVerifier:
    """Reuse the local encoders and their category vectors across Telegram turns."""

    return LocalCatalogCategoryVerifier(Path(cache_root))
