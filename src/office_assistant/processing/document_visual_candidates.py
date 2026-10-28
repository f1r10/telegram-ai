"""Find unverified visual evidence in active PDF, DOCX and XLSX documents.

The worker registers every supported embedded image during ingestion. The
lookup uses current ACTIVE parent files, original occurrence locators, and
verified image content, never an invented SKU, price or inventory assertion.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import (
    FileObject,
    FileObjectState,
    ImageAsset,
    ImageAssetOccurrence,
    ImageAssetOrigin,
)
from office_assistant.processing.catalog_visual_candidates import (
    SEMANTIC_ALTERNATIVE_MARGIN,
    PdfImageSource,
    match_pdf_image_sources,
)
from office_assistant.processing.image_similarity import VisualCandidate, shortlist
from office_assistant.processing.semantic_visual import SemanticReference, SemanticVisualSearch

DOCUMENT_IMAGE_ORIGINS = (
    ImageAssetOrigin.STANDALONE_UPLOAD,
    ImageAssetOrigin.PDF_EMBEDDED,
    ImageAssetOrigin.DOCX_EMBEDDED,
    ImageAssetOrigin.XLSX_EMBEDDED,
)
MAX_DOCUMENT_IMAGES = 500


@dataclass(frozen=True, slots=True)
class DocumentImageSource:
    occurrence_id: int
    source_file_id: int
    filename: str
    origin: str
    locator: dict[str, object]
    storage_key: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class DocumentImageHit:
    source: DocumentImageSource
    match_type: str
    match_score: float | None = None


async def active_document_images(
    session: AsyncSession, *, limit: int = MAX_DOCUMENT_IMAGES
) -> tuple[list[DocumentImageSource], bool]:
    """Read any indexed image, standalone or embedded, of an ACTIVE parent file.

    Return a truncation flag instead of misrepresenting a 500-picture sample
    as coverage of all documents.
    """

    if not 1 <= limit <= MAX_DOCUMENT_IMAGES:
        raise ValueError("Invalid document image limit")
    rows = (
        await session.execute(
            select(
                ImageAssetOccurrence.id,
                ImageAssetOccurrence.source_file_id,
                FileObject.original_filename,
                ImageAssetOccurrence.origin_type,
                ImageAssetOccurrence.locator,
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
            .order_by(ImageAssetOccurrence.id.desc())
            .limit(limit + 1)
        )
    ).all()
    sources = [
        DocumentImageSource(occurrence_id, file_id, filename, origin, locator, key, digest)
        for occurrence_id, file_id, filename, origin, locator, key, digest in rows[:limit]
    ]
    return sources, len(rows) > limit


def find_document_images(
    image: bytes,
    sources: list[DocumentImageSource],
    storage_root: Path,
    *,
    semantic: SemanticVisualSearch | None = None,
    limit: int = 3,
    quick_only: bool = False,
) -> tuple[tuple[DocumentImageHit, ...], bool, bool]:
    """Near duplicate first, opt-in cross-angle semantic next, always source-only."""

    if not 1 <= limit <= 10:
        raise ValueError("Invalid source candidate limit")
    candidates = sources[:MAX_DOCUMENT_IMAGES]
    indexed = {str(item.occurrence_id): item for item in candidates}
    visual = [
        VisualCandidate(str(item.occurrence_id), item.storage_key, item.content_sha256)
        for item in candidates
    ]
    matches = shortlist(image, visual, storage_root, max_results=limit)
    ids = [item.product_code for item in matches]
    if not ids:
        pdf: list[PdfImageSource] = []
        for item in candidates:
            if item.origin != ImageAssetOrigin.PDF_EMBEDDED:
                continue
            page = item.locator.get("page")
            index = item.locator.get("image_index")
            if (
                not isinstance(page, int)
                or isinstance(page, bool)
                or not isinstance(index, int)
                or isinstance(index, bool)
            ):
                continue
            pdf.append(
                PdfImageSource(
                    item.occurrence_id,
                    item.filename,
                    page,
                    index,
                    item.storage_key,
                    item.content_sha256,
                    source_file_id=item.source_file_id,
                )
            )
        if pdf:
            ids = [
                str(item.occurrence_id)
                for item in match_pdf_image_sources(image, pdf, storage_root, limit=limit)
                if item.occurrence_id is not None
            ]
    if ids:
        hits = tuple(
            DocumentImageHit(indexed[key], "near_duplicate") for key in ids if key in indexed
        )
        return hits, len(sources) > MAX_DOCUMENT_IMAGES, False

    if quick_only or semantic is None or not candidates:
        return (), len(sources) > MAX_DOCUMENT_IMAGES, False

    refs = [
        SemanticReference(str(item.occurrence_id), item.storage_key, item.content_sha256)
        for item in candidates
    ]
    result = semantic.search(image, refs, limit=limit)
    best = result.candidates[0].score if result.candidates else None
    hits = tuple(
        DocumentImageHit(indexed[candidate.product_code], "semantic", candidate.score)
        for candidate in result.candidates
        if candidate.product_code in indexed
        and best is not None
        and best - candidate.score <= SEMANTIC_ALTERNATIVE_MARGIN
    )
    return hits, len(sources) > MAX_DOCUMENT_IMAGES or result.incomplete, result.unavailable


def document_image_location(source: DocumentImageSource) -> str:
    locator = source.locator
    if source.origin == ImageAssetOrigin.STANDALONE_UPLOAD:
        return "ayrıca saxlanmış şəkil"
    if source.origin == ImageAssetOrigin.PDF_EMBEDDED:
        page = locator.get("page")
        return f"səhifə {page}" if type(page) is int and page > 0 else "səhifə naməlum"
    if source.origin == ImageAssetOrigin.XLSX_EMBEDDED:
        sheet = locator.get("sheet")
        cell = locator.get("anchor_cell")
        name = " ".join(sheet.split())[:60] if isinstance(sheet, str) else "naməlum vərəq"
        position = f", xana {cell}" if isinstance(cell, str) and len(cell) <= 16 else ""
        return f"vərəq {name}{position}"
    if source.origin == ImageAssetOrigin.DOCX_EMBEDDED:
        role = locator.get("role")
        if isinstance(role, str) and role:
            return f"bölmə: {' '.join(role.split())[:40]}"
        if type(locator.get("table_index")) is int:
            row = locator.get("row")
            return f"cədvəl, sətir {row}" if type(row) is int else "cədvəl"
        index = locator.get("paragraph_index")
        return f"abzas {index + 1}" if type(index) is int else "sənəd daxilində"
    return "mənbə yeri göstərilməyib"


def render_document_image_hits(
    hits: tuple[DocumentImageHit, ...], *, incomplete: bool = False
) -> str:
    """Show source provenance, not product identity or invented PDF page labels."""

    if not hits:
        return ""
    lines = ["📁 FAYLLARDA ŞƏKİL ÜZRƏ NƏTİCƏ", ""]
    for position, hit in enumerate(hits, start=1):
        filename = " ".join(hit.source.filename.split())[:96]
        kind = hit.source.origin.replace("_EMBEDDED", "")
        lines.append(f"{position}. {filename} ({kind})")
        lines.append(f"   Yer: {document_image_location(hit.source)}")
        lines.append(
            "   Uyğunluq: fərqli foto üzrə ehtimal"
            if hit.match_type == "semantic"
            else "   Uyğunluq: vizual yaxın şəkil"
        )
        lines.append("")
    lines.append(
        "Bu nəticələr sənədlərin içindəki şəkillərə əsaslanan "
        "təsdiqlənməmiş mənbələrdir. Məhsulun dəqiq kodunu, qiymətini "
        "və anbar qalığını təsdiqləmir. Mənbə faylını açıb yoxlayın."
    )
    if incomplete:
        lines.append("Yoxlama ilk 500 aktiv sənəd şəkli qeydi ilə məhdudlaşıb.")
    return "\n".join(lines)
