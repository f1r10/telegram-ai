"""Conservative near-duplicate image ranking for user-confirmed product reference photos.

This is NOT semantic/product identification. It tolerates resized/recompressed photos
but cannot identify an unrelated viewing angle and never makes authoritative stock claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import cast

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
DHASH_DISTANCE_LIMIT = 4
AHASH_DISTANCE_LIMIT = 8
# Near-duplicate mode must not equate unrelated objects with similar gray geometry.
MAX_COLOR_TILE_DIFFERENCE = 22.0
# Finer spatial colour grid blocks coarse-hash collisions between unrelated objects.
MAX_FINE_TILE_DIFFERENCE = 18.0


@dataclass(frozen=True, slots=True)
class ImageFingerprint:
    dhash: int
    ahash: int
    color_tiles: tuple[int, ...]
    fine_tiles: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class VisualCandidate:
    product_code: str
    storage_key: str
    content_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class VisualMatch:
    product_code: str
    dhash_distance: int
    ahash_distance: int


def fingerprint(data: bytes) -> ImageFingerprint | None:
    """Decode safe images and compute two 64-bit grayscale fingerprints.

    Uniform swatches/logos are excluded: comparing them with a product image
    would produce many false positives.
    """

    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    try:
        with Image.open(BytesIO(data)) as raw:
            if raw.width * raw.height > MAX_IMAGE_PIXELS:
                return None
            oriented = ImageOps.exif_transpose(raw).convert("RGB")
            color_grid = oriented.resize((4, 4), Image.Resampling.LANCZOS)
            colors = cast(list[tuple[int, int, int]], list(color_grid.get_flattened_data()))
            color_tiles = tuple(channel for pixel in colors for channel in pixel)
            fine_grid = oriented.resize((16, 16), Image.Resampling.LANCZOS)
            fine_colors = cast(list[tuple[int, int, int]], list(fine_grid.get_flattened_data()))
            fine_tiles = tuple(channel for pixel in fine_colors for channel in pixel)
            image = oriented.convert("L")
            small = image.resize((9, 8), Image.Resampling.LANCZOS)
            a_small = image.resize((8, 8), Image.Resampling.LANCZOS)
            # L-mode pixels are 8-bit scalars; Pillow's general stubs also admit tuples.
            pixels = cast(list[int], list(small.get_flattened_data()))
            apixels = cast(list[int], list(a_small.get_flattened_data()))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None
    if max(apixels) - min(apixels) < 12:
        return None
    average = sum(apixels) / 64
    ahash = 0
    for value in apixels:
        ahash = (ahash << 1) | int(value >= average)
    dhash = 0
    for row in range(8):
        for column in range(8):
            left = pixels[row * 9 + column]
            right = pixels[row * 9 + column + 1]
            dhash = (dhash << 1) | int(left > right)
    return ImageFingerprint(dhash, ahash, color_tiles, fine_tiles)


def fingerprint_near_duplicate(query: ImageFingerprint, reference: ImageFingerprint) -> bool:
    """Require texture, coarse color AND fine spatial layout agreement.

    This identifies a possible recompressed/resized duplicate, never product identity.
    Separate views belong to the calibrated semantic retrieval path.
    """

    dh = (query.dhash ^ reference.dhash).bit_count()
    ah = (query.ahash ^ reference.ahash).bit_count()
    if dh > DHASH_DISTANCE_LIMIT or ah > AHASH_DISTANCE_LIMIT:
        return False
    coarse = sum(
        abs(left - right)
        for left, right in zip(query.color_tiles, reference.color_tiles, strict=True)
    ) / len(query.color_tiles)
    if coarse > MAX_COLOR_TILE_DIFFERENCE:
        return False
    fine = sum(
        abs(left - right)
        for left, right in zip(query.fine_tiles, reference.fine_tiles, strict=True)
    ) / len(query.fine_tiles)
    return fine <= MAX_FINE_TILE_DIFFERENCE


@lru_cache(maxsize=2048)
def _cached_file_fingerprint(path: str, size: int, mtime_ns: int) -> ImageFingerprint | None:
    """Cache decoded reference signatures without caching document bytes."""

    del size, mtime_ns
    return fingerprint(Path(path).read_bytes())


def shortlist(
    query_bytes: bytes,
    references: list[VisualCandidate],
    storage_root: Path,
    *,
    max_results: int = 3,
) -> list[VisualMatch]:
    """Return conservative possible visual matches, sorted by distance.

    Never rank an invalid/unreadable input. Caller must scope the references to
    explicitly confirmed active product-image relations and enforce a bounded set.
    """

    query_hash = fingerprint(query_bytes)
    if query_hash is None:
        return []
    best_by_product: dict[str, VisualMatch] = {}
    for item in references:
        try:
            path = (storage_root / item.storage_key).resolve()
            path.relative_to(storage_root.resolve())
            if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
                continue
            stat = path.stat()
            ref_hash = _cached_file_fingerprint(str(path), stat.st_size, stat.st_mtime_ns)
        except (OSError, ValueError):
            continue
        if ref_hash is None:
            continue
        d = (query_hash.dhash ^ ref_hash.dhash).bit_count()
        a = (query_hash.ahash ^ ref_hash.ahash).bit_count()
        if not fingerprint_near_duplicate(query_hash, ref_hash):
            continue
        # Fingerprint similarity is not proof that the current file is the
        # human-approved reference. In live lookups the caller supplies the
        # confirmed ImageAsset SHA; reject edited, stale or corrupt reference
        # bytes even when an old fingerprint exists in the local file cache.
        if item.content_sha256 is not None:
            try:
                body = path.read_bytes()
            except OSError:
                continue
            if len(body) > MAX_IMAGE_BYTES or sha256(body).hexdigest() != item.content_sha256:
                continue
        match = VisualMatch(item.product_code, d, a)
        existing = best_by_product.get(item.product_code)
        if existing is None or (d + a, d, a) < (
            existing.dhash_distance + existing.ahash_distance,
            existing.dhash_distance,
            existing.ahash_distance,
        ):
            best_by_product[item.product_code] = match
    return sorted(
        best_by_product.values(),
        key=lambda item: (item.dhash_distance + item.ahash_distance, item.product_code),
    )[:max_results]
