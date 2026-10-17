"""Opt-in, offline-only CLIP image candidate retrieval.

A similarity is NEVER an identification, confirmation, stock or price fact.
Only active, human-confirmed references supplied by the database may be scored.
No model download or vendor call occurs on a Telegram turn.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Protocol, cast

from PIL import Image, ImageOps, UnidentifiedImageError

from office_assistant.processing.image_similarity import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS

CLIP_MODEL = "Qdrant/clip-ViT-B-32-vision"
CLIP_DIMENSION = 512
MAX_REFERENCES = 80
MODEL_VERSION = "clip-vit-b32-v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")


class ImageEncoder(Protocol):
    def embed(self, paths: list[Path]) -> list[list[float]]: ...


class LocalClipEncoder:
    """Model loading is local_files_only: an absent model cannot contact Hugging Face."""

    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root
        self._model: object | None = None

    def embed(self, paths: list[Path]) -> list[list[float]]:
        # Imported lazily: current image lookup has no model load or cost
        # unless the feature flag was explicitly enabled.
        from fastembed import ImageEmbedding

        if self._model is None:
            self._model = ImageEmbedding(
                model_name=CLIP_MODEL,
                cache_dir=str(self.cache_root),
                local_files_only=True,
                threads=2,
            )
        # FastEmbed returns numpy arrays; the protocol boundary keeps those
        # implementation types outside application/Telegram code.
        model = cast(ImageEmbedding, self._model)
        return [[float(value) for value in vector] for vector in model.embed(paths)]


@dataclass(frozen=True, slots=True)
class SemanticReference:
    product_code: str
    storage_key: str
    content_sha256: str


def balanced_references(
    references: list[SemanticReference], *, limit: int
) -> list[SemanticReference]:
    """Fair, deterministic sample: first view of every SKU before second views.

    The caller supplies references ordered newest-first. Preserve the order
    within a product, but do not let a SKU with dozens of angles monopolize
    the bounded offline CLIP inference window. No additional identities are
    inferred and no unconfirmed photos enter the reference list.
    """

    if not 1 <= limit <= 500:
        raise ValueError("Invalid balanced reference limit")
    buckets: dict[str, list[SemanticReference]] = {}
    for reference in references:
        buckets.setdefault(reference.product_code, []).append(reference)
    chosen: list[SemanticReference] = []
    positions = {code: 0 for code in buckets}
    while len(chosen) < limit:
        progressed = False
        for code, group in buckets.items():
            index = positions[code]
            if index >= len(group):
                continue
            chosen.append(group[index])
            positions[code] += 1
            progressed = True
            if len(chosen) >= limit:
                break
        if not progressed:
            break
    return chosen


@dataclass(frozen=True, slots=True)
class SemanticCandidate:
    product_code: str
    score: float


@dataclass(frozen=True, slots=True)
class SemanticSearchResult:
    candidates: tuple[SemanticCandidate, ...]
    inspected: int
    incomplete: bool
    unavailable: bool = False


@dataclass(frozen=True, slots=True)
class ReferenceWarmResult:
    inspected: int
    warmed: int
    skipped: int
    incomplete: bool
    unavailable: bool = False


def unit_vector(values: list[float]) -> tuple[float, ...] | None:
    if len(values) != CLIP_DIMENSION or not all(math.isfinite(value) for value in values):
        return None
    length = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(length) or length <= 1e-12:
        return None
    return tuple(value / length for value in values)


def _thumbnail(data: bytes, path: Path) -> bool:
    """Bound source bytes, decoded pixels and inference input dimensions."""

    if not data or len(data) > MAX_IMAGE_BYTES:
        return False
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                return False
            if source.width < 32 or source.height < 32:
                return False
            if source.width * source.height > MAX_IMAGE_PIXELS:
                return False
            picture = ImageOps.exif_transpose(source).convert("RGB")
            picture.thumbnail((448, 448), Image.Resampling.LANCZOS)
            picture.save(path, format="PNG")
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return False
    return True


def _read_cached(path: Path, digest: str, version: str) -> tuple[float, ...] | None:
    try:
        if not path.is_file() or path.stat().st_size > 32_768:
            return None
        data: object = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict):
            return None
        fields = cast(dict[str, object], data)
        vector = fields.get("vector")
        if fields.get("sha256") != digest or fields.get("version") != version:
            return None
        if not isinstance(vector, list):
            return None
        raw = cast(list[object], vector)
        if len(raw) != CLIP_DIMENSION:
            return None
        numbers = [
            float(item)
            for item in raw
            if isinstance(item, (int, float)) and not isinstance(item, bool)
        ]
        if len(numbers) != CLIP_DIMENSION:
            return None
        return unit_vector(numbers)
    except (OSError, ValueError, TypeError):
        return None


def _write_cached(path: Path, digest: str, version: str, vector: tuple[float, ...]) -> None:
    """Versioned, atomic local cache; not a source of authoritative product claims."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".image-vector-", delete=False
        ) as handle:
            temp_name = handle.name
            os.chmod(temp_name, 0o600)
            json.dump(
                {"sha256": digest, "version": version, "vector": list(vector)},
                handle,
                separators=(",", ":"),
            )
        os.replace(temp_name, path)
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


class SemanticVisualSearch:
    """Synchronous bounded image scoring; invoke from an isolated executor."""

    def __init__(
        self,
        *,
        storage_root: Path,
        model_cache_root: Path,
        encoder: ImageEncoder | None = None,
        version: str = MODEL_VERSION,
        min_score: float = 0.60,
        max_references: int = MAX_REFERENCES,
    ) -> None:
        if not _VERSION.fullmatch(version):
            raise ValueError("Invalid local vision model version")
        if not 0.0 <= min_score <= 1.0 or not math.isfinite(min_score):
            raise ValueError("Invalid visual candidate display threshold")
        if not 1 <= max_references <= 500:
            raise ValueError("Invalid visual reference limit")
        self.storage_root = storage_root.resolve()
        self.cache_root = model_cache_root.resolve()
        self.encoder = encoder or LocalClipEncoder(self.cache_root)
        self.version = version
        self.min_score = min_score
        self.max_references = max_references
        self._lock = threading.Lock()

    def _vector(
        self, data: bytes, digest: str, *, cacheable: bool = True
    ) -> tuple[float, ...] | None:
        location = (
            self.cache_root / "visual-embeddings" / self.version / digest[:2] / (digest + ".json")
        )
        if cacheable:
            cached = _read_cached(location, digest, self.version)
            if cached is not None:
                return cached
        with tempfile.TemporaryDirectory(prefix="office-visual-", dir=None) as work:
            image_path = Path(work) / "input.png"
            if not _thumbnail(data, image_path):
                return None
            vectors = self.encoder.embed([image_path])
        if len(vectors) != 1:
            return None
        normal = unit_vector(vectors[0])
        if normal is not None and cacheable:
            try:
                _write_cached(location, digest, self.version, normal)
            except OSError:
                # The embedding remains usable even on a read-only cache mount.
                pass
        return normal

    def _verified_reference(self, reference: SemanticReference) -> bytes | None:
        """Re-check every active reference's source bytes against the content identity."""

        if not _SHA.fullmatch(reference.content_sha256):
            return None
        try:
            full = (self.storage_root / reference.storage_key).resolve()
            full.relative_to(self.storage_root)
            if not full.is_file() or not 0 < full.stat().st_size <= MAX_IMAGE_BYTES:
                return None
            body = full.read_bytes()
        except (OSError, ValueError):
            return None
        return body if sha256(body).hexdigest() == reference.content_sha256 else None

    def warm_references(self, references: list[SemanticReference]) -> ReferenceWarmResult:
        """Explicit operator-only precomputation; no question photos are retained."""

        selected = balanced_references(references, limit=self.max_references)
        incomplete = len(references) > len(selected)
        if not self._lock.acquire(blocking=False):
            return ReferenceWarmResult(0, 0, 0, incomplete, unavailable=True)
        inspected = warmed = skipped = 0
        try:
            for reference in selected:
                inspected += 1
                body = self._verified_reference(reference)
                if body is None:
                    skipped += 1
                    continue
                try:
                    if self._vector(body, reference.content_sha256) is None:
                        skipped += 1
                    else:
                        warmed += 1
                except (OSError, ValueError, RuntimeError, ImportError):
                    return ReferenceWarmResult(
                        inspected, warmed, skipped, incomplete, unavailable=True
                    )
            return ReferenceWarmResult(inspected, warmed, skipped, incomplete)
        finally:
            self._lock.release()

    def search(
        self,
        image_bytes: bytes,
        references: list[SemanticReference],
        *,
        limit: int = 3,
    ) -> SemanticSearchResult:
        if not 1 <= limit <= 10:
            raise ValueError("Invalid semantic candidate limit")
        # A busy image model is not allowed to stall unrelated Telegram turns.
        if not self._lock.acquire(blocking=False):
            return SemanticSearchResult((), 0, bool(references), unavailable=True)
        try:
            return self._search_locked(image_bytes, references, limit=limit)
        finally:
            self._lock.release()

    def _search_locked(
        self, image_bytes: bytes, references: list[SemanticReference], *, limit: int
    ) -> SemanticSearchResult:
        if len(image_bytes) > MAX_IMAGE_BYTES:
            return SemanticSearchResult((), 0, False)
        selected = balanced_references(references, limit=self.max_references)
        incomplete = len(references) > len(selected)
        inspected = 0
        try:
            # A question photo is transient, not user-confirmed reference content:
            # never retain its image-derived vector on disk.
            query = self._vector(image_bytes, sha256(image_bytes).hexdigest(), cacheable=False)
            if query is None:
                return SemanticSearchResult((), 0, incomplete)
            scored: dict[str, float] = {}
            for reference in selected:
                body = self._verified_reference(reference)
                if body is None:
                    continue
                try:
                    vector = self._vector(body, reference.content_sha256)
                except (OSError, ValueError):
                    continue
                if vector is None:
                    continue
                inspected += 1
                score = sum(a * b for a, b in zip(query, vector, strict=True))
                if not math.isfinite(score) or score < self.min_score:
                    continue
                scored[reference.product_code] = max(
                    scored.get(reference.product_code, -1.0), min(1.0, score)
                )
            candidates = tuple(
                SemanticCandidate(code, round(score, 4))
                for code, score in sorted(scored.items(), key=lambda x: (-x[1], x[0]))[:limit]
            )
            return SemanticSearchResult(candidates, inspected, incomplete)
        except (OSError, ValueError, RuntimeError, ImportError):
            # No exception message contains private image bytes, and absence of
            # a locally provisioned CLIP model is a normal deployment state.
            return SemanticSearchResult((), inspected, incomplete, unavailable=True)
