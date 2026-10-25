"""Read-only, non-authoritative visual candidates from pictures embedded in PDFs.

A PDF page and image occurrence are evidence of a picture in a document, not a
product-image relation. This matcher may point to a source page; it MUST NOT
infer a SKU, offer stock, or create/confirm a product from page proximity.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import cast

from PIL import Image, ImageOps, UnidentifiedImageError

from office_assistant.processing.semantic_visual import (
    SemanticReference,
    SemanticVisualSearch,
)
from office_assistant.processing.image_similarity import (
    AHASH_DISTANCE_LIMIT,
    DHASH_DISTANCE_LIMIT,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    ImageFingerprint,
    VisualCandidate,
    fingerprint,
    shortlist,
)

MAX_PDF_REFERENCES = 500


@dataclass(frozen=True, slots=True)
class PdfImageSource:
    occurrence_id: int
    filename: str
    page: int
    image_index: int
    storage_key: str
    content_sha256: str
    source_file_id: int | None = None


@dataclass(frozen=True, slots=True)
class PdfPageCandidate:
    filename: str
    page: int
    image_index: int
    source_file_id: int | None = None
    occurrence_id: int | None = None
    nearby_label: str | None = None
    visual_row: int | None = None
    match_type: str = "near_duplicate"


def _white_background_fingerprint(data: bytes) -> ImageFingerprint | None:
    """Remove purely white screenshot padding before a conservative hash test.

    This is supplementary to ordinary near-duplicate hashing, not semantic
    identification. Do not trim photos with non-white borders/backgrounds.
    """

    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    try:
        with Image.open(BytesIO(data)) as raw:
            if raw.width * raw.height > MAX_IMAGE_PIXELS:
                return None
            image = ImageOps.exif_transpose(raw).convert("RGB")
            gray = ImageOps.grayscale(image)
            corners = (
                cast(int, gray.getpixel((0, 0))),
                cast(int, gray.getpixel((gray.width - 1, 0))),
                cast(int, gray.getpixel((0, gray.height - 1))),
                cast(int, gray.getpixel((gray.width - 1, gray.height - 1))),
            )
            if min(corners) < 235:
                return None
            bbox = gray.point(lambda pixel: 255 if cast(int, pixel) < 235 else 0).getbbox()
            if bbox is None:
                return None
            image = image.crop(bbox)
            with BytesIO() as output:
                image.save(output, format="PNG")
                return fingerprint(output.getvalue())
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None


@lru_cache(maxsize=2048)
def _cached_white_background_fingerprint(
    path: str, size: int, mtime_ns: int
) -> ImageFingerprint | None:
    del size, mtime_ns
    try:
        return _white_background_fingerprint(Path(path).read_bytes())
    except OSError:
        return None


def _white_border_matches(
    query: bytes,
    sources: list[PdfImageSource],
    storage_root: Path,
    *,
    limit: int,
) -> list[str]:
    query_hash = _white_background_fingerprint(query)
    if query_hash is None:
        return []
    ranked: list[tuple[int, int, str]] = []
    for item in sources:
        try:
            path = (storage_root / item.storage_key).resolve()
            path.relative_to(storage_root.resolve())
            if not path.is_file():
                continue
            stat = path.stat()
            if not 0 < stat.st_size <= MAX_IMAGE_BYTES:
                continue
            # Never trust a stale/corrupted PDF-derived cache.
            data = path.read_bytes()
            if sha256(data).hexdigest() != item.content_sha256:
                continue
            reference = _cached_white_background_fingerprint(
                str(path), stat.st_size, stat.st_mtime_ns
            )
        except (OSError, ValueError):
            continue
        if reference is None:
            continue
        dh = (query_hash.dhash ^ reference.dhash).bit_count()
        ah = (query_hash.ahash ^ reference.ahash).bit_count()
        if dh <= DHASH_DISTANCE_LIMIT and ah <= AHASH_DISTANCE_LIMIT:
            ranked.append((dh + ah, dh, str(item.occurrence_id)))
    ranked.sort()
    return [code for _, _, code in ranked[:limit]]


def match_pdf_image_sources(
    query_bytes: bytes,
    sources: list[PdfImageSource],
    storage_root: Path,
    *,
    limit: int = 3,
) -> tuple[PdfPageCandidate, ...]:
    """Return conservative source locators only, with digest-checked image bytes.

    Do not silently promote the adjacent text on a PDF page to a verified label:
    the existing PDF parser records page/XObject but not image-text geometry.
    """

    if not 1 <= limit <= 10:
        raise ValueError("Invalid catalog candidate limit")
    rows = [
        item
        for item in sources[:MAX_PDF_REFERENCES]
        if item.occurrence_id > 0 and item.page > 0 and item.image_index > 0
    ]
    indexed = {str(item.occurrence_id): item for item in rows}
    references = [
        VisualCandidate(str(item.occurrence_id), item.storage_key, item.content_sha256)
        for item in rows
    ]
    matches = shortlist(query_bytes, references, storage_root, max_results=limit)
    match_ids = [item.product_code for item in matches]
    if len(match_ids) < limit:
        for match_id in _white_border_matches(query_bytes, rows, storage_root, limit=limit):
            if match_id not in match_ids:
                match_ids.append(match_id)
            if len(match_ids) >= limit:
                break
    # Repeated occurrences of the same artwork on one page are not different
    # product candidates. Preserve the original source page and locator.
    unique: dict[tuple[str, int, int], PdfPageCandidate] = {}
    for match_id in match_ids:
        source = indexed.get(match_id)
        if source is None:
            continue
        key = (source.filename, source.page, source.image_index)
        unique.setdefault(
            key,
            PdfPageCandidate(
                source.filename,
                source.page,
                source.image_index,
                source_file_id=source.source_file_id,
                occurrence_id=source.occurrence_id,
            ),
        )
    return tuple(unique.values())


def semantic_pdf_image_candidates(
    query_bytes: bytes,
    sources: list[PdfImageSource],
    semantic: SemanticVisualSearch,
    *,
    limit: int = 3,
) -> tuple[tuple[PdfPageCandidate, ...], bool, bool]:
    """Find different photographs using the opt-in local CLIP encoder.

    PDF hits are unverified evidence only. Never treat a similarity vector
    as an approved product, identity match, price, or inventory record.
    """

    if not 1 <= limit <= 10:
        raise ValueError("Invalid catalog semantic limit")
    allowed = {
        str(item.occurrence_id): item
        for item in sources[:MAX_PDF_REFERENCES]
        if item.occurrence_id > 0 and item.source_file_id is not None
    }
    if not allowed:
        return (), False, False
    refs = [
        SemanticReference(key, item.storage_key, item.content_sha256)
        for key, item in allowed.items()
    ]
    outcome = semantic.search(query_bytes, refs, limit=limit)
    matches: list[PdfPageCandidate] = []
    used: set[tuple[int, int]] = set()
    for item in outcome.candidates:
        source = allowed.get(item.product_code)
        if source is None or source.source_file_id is None:
            continue
        key = (source.source_file_id, source.image_index)
        if key in used:
            continue
        used.add(key)
        matches.append(
            PdfPageCandidate(
                source.filename,
                source.page,
                source.image_index,
                source_file_id=source.source_file_id,
                occurrence_id=source.occurrence_id,
                match_type="semantic",
            )
        )
    return tuple(matches), outcome.incomplete, outcome.unavailable


def render_pdf_image_candidates(
    matches: tuple[PdfPageCandidate, ...], *, incomplete: bool = False
) -> str:
    """A bounded, clearly non-authoritative source-page preview."""

    if not matches:
        return ""
    lines: list[str] = []
    for item in matches:
        # Filenames are untrusted upload metadata and are never treated as
        # formatting instructions or control characters.
        filename = " ".join(item.filename.split())[:96]
        location = f"səhifə {item.page}"
        if item.visual_row is not None:
            location += f", yuxarıdan {item.visual_row}-ci məhsul şəkli"
        lines.append(f"• {filename} — {location}")
        if item.match_type == "semantic":
            lines.append("   Müxtəlif fotolar üzrə semantik oxşarlıq: təsdiqlənməmiş namizəd")
        if item.nearby_label:
            lines.append(f"   PDF-də yaxın məhsul sətri: {item.nearby_label}")
    limit_note = "\nYoxlama yalnız ilk 500 PDF şəkil qeydi ilə məhdudlaşıb." if incomplete else ""
    return (
        "📄 PDF KATALOQDA VİZUAL OXŞAR ŞƏKİLLƏR\n\n"
        + "\n".join(lines)
        + "\n\nBu yalnız PDF-dəki şəkillərə mümkün vizual uyğunluqdur. "
        "Şəkillə yanaşı göstərilən mətn PDF-nin sətir quruluşuna görə "
        "avtomatik əlaqələndirilmiş namizəddir, rəsmi məhsul təsdiqi deyil. "
        "Məhsul kodu, eynilik, qiymət və anbar qalığı təsdiqlənmir. "
        "Səhifəni orijinal PDF-də yoxlayın." + limit_note
    )
