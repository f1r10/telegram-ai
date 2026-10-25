"""Read-only, non-authoritative visual candidates from pictures embedded in PDFs.

A PDF page and image occurrence are evidence of a picture in a document, not a
product-image relation. This matcher may point to a source page; it MUST NOT
infer a SKU, offer stock, or create/confirm a product from page proximity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from office_assistant.processing.image_similarity import VisualCandidate, shortlist

MAX_PDF_REFERENCES = 500


@dataclass(frozen=True, slots=True)
class PdfImageSource:
    occurrence_id: int
    filename: str
    page: int
    image_index: int
    storage_key: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class PdfPageCandidate:
    filename: str
    page: int
    image_index: int


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
    # Repeated occurrences of the same artwork on one page are not different
    # product candidates. Preserve the original source page and locator.
    unique: dict[tuple[str, int, int], PdfPageCandidate] = {}
    for match in matches:
        source = indexed.get(match.product_code)
        if source is None:
            continue
        key = (source.filename, source.page, source.image_index)
        unique.setdefault(
            key,
            PdfPageCandidate(source.filename, source.page, source.image_index),
        )
    return tuple(unique.values())


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
        lines.append(f"• {filename} — səhifə {item.page}, şəkil {item.image_index}")
    limit_note = (
        "\nYoxlama yalnız ilk 500 PDF şəkil qeydi ilə məhdudlaşıb."
        if incomplete
        else ""
    )
    return (
        "📄 PDF KATALOQDA VİZUAL OXŞAR ŞƏKİLLƏR\n\n"
        + "\n".join(lines)
        + "\n\nBu yalnız PDF-dəki şəkillərə mümkün vizual uyğunluqdur. "
        "Məhsul adı, kodu, eyniliyi və anbar qalığı təsdiqlənmir. "
        "Dəqiq məhsul təsviri üçün göstərilən səhifədəki sətri yoxlayın."
        + limit_note
    )
