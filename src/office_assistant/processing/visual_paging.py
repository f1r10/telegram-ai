"""Bounded ranked pages from a full active ANN corpus, never a corpus cap."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from office_assistant.processing.document_visual_candidates import document_image_location
from office_assistant.processing.visual_ann import (
    IndexedDocument,
    IndexedProduct,
    IndexedResults,
)

PAGE_SIZE = 5


@dataclass(frozen=True, slots=True)
class RankedPage:
    text: str
    docs: tuple[IndexedDocument, ...]
    page: int
    pages: int
    has_previous: bool
    has_next: bool


def indexed_page(
    result: IndexedResults, page: int, *, size: int = PAGE_SIZE
) -> RankedPage:
    if size < 1 or size > 10 or page < 0:
        raise ValueError("Invalid visual page")
    merged: list[tuple[str, float, IndexedProduct | IndexedDocument]] = [
        ("product", item.score, item) for item in result.products
    ]
    merged.extend(("document", item.score, item) for item in result.documents)
    merged.sort(key=lambda candidate: -candidate[1])
    pages = max(1, ceil(len(merged) / size))
    if page >= pages:
        raise ValueError("Visual page no longer exists")

    selected = merged[page * size : (page + 1) * size]
    lines = [
        "📁 DINOv2 — BÜTÜN AKTİV ŞƏKİL İNDEKSİ",
        f"Nəticə: {len(merged)} ən güclü namizəd | Səhifə: {page+1}/{pages}",
        "",
    ]
    docs: list[IndexedDocument] = []
    for position, (kind, score, item) in enumerate(selected, start=page * size + 1):
        if kind == "product" and isinstance(item, IndexedProduct):
            lines.extend(
                [
                    f"{position}. 📦 Təsdiqli kataloq nümunəsi: {item.code}",
                    f"   Oxşarlıq balı: {score:.3f} (əminlik faizi deyil)",
                    "",
                ]
            )
        elif isinstance(item, IndexedDocument):
            docs.append(item)
            filename = " ".join(item.source.filename.split())[:100]
            lines.extend(
                [
                    f"{position}. 🖼 Mənbə: {filename}",
                    f"   Yeri: {document_image_location(item.source)}",
                    *(
                        [f"   Məlumat: {item.source.description[:320]}"]
                        if item.source.description
                        else []
                    ),
                    f"   Oxşarlıq balı: {score:.3f} (əminlik faizi deyil)",
                    "",
                ]
            )
    if not merged:
        lines.append("Bu şəkil üçün kifayət qədər güclü namizəd tapılmadı.")
    lines.append(
        "Nəticələr foto oxşarlığına görə sıralanıb. Məhsulun dəqiq "
        "kimliyi, qiyməti və anbar qalığı avtomatik təsdiqlənmir."
    )
    if len(merged) >= 40:
        lines.append("Göstərilənlər indeksin ilk 40 namizədidir, ümumi arxiv limiti deyil.")
    return RankedPage(
        "\n".join(lines),
        tuple(docs),
        page,
        pages,
        page > 0,
        page < pages - 1,
    )
