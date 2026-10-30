"""Telegram renders ranked 5-item pages from full DINO/ANN candidate top-K."""

import pytest

from office_assistant.infrastructure.database.models import ImageAssetOrigin
from office_assistant.processing.document_visual_candidates import DocumentImageSource
from office_assistant.processing.visual_ann import (
    IndexedDocument,
    IndexedProduct,
    IndexedResults,
)
from office_assistant.processing.visual_paging import indexed_page


def test_visual_paging_uses_one_score_order_for_products_and_documents() -> None:
    source = DocumentImageSource(
        90,
        12,
        "watch-catalog.xlsx",
        ImageAssetOrigin.XLSX_EMBEDDED,
        {"sheet": "Saatlar", "anchor_cell": "C9"},
        "img.png",
        "a" * 64,
        "Ad: Qara qol saatı",
    )
    results = IndexedResults(
        tuple(IndexedProduct(f"KA{i:02}", 0.99 - i * 0.01) for i in range(11)),
        (IndexedDocument(source, 0.965),),
    )
    first = indexed_page(results, 0)
    second = indexed_page(results, 1)
    third = indexed_page(results, 2)
    assert first.pages == 3
    assert first.has_next and not first.has_previous
    assert first.text.index("KA02") < first.text.index("watch-catalog.xlsx")
    assert first.text.index("watch-catalog.xlsx") < first.text.index("KA03")
    assert [slot for slot, _product in first.product_choices] == [0, 1, 2, 4]
    assert first.docs[0].source.description.startswith("Ad:")
    assert "Saatlar" in first.text and "Qara qol saatı" in first.text
    assert second.has_previous and second.has_next
    assert third.has_previous and not third.has_next
    assert "Səhifə: 3/3" in third.text
    with pytest.raises(ValueError):
        indexed_page(results, 3)


def test_empty_results_remain_safe_without_invented_sku() -> None:
    page = indexed_page(IndexedResults((), ()), 0)
    assert page.pages == 1
    assert "namizəd tapılmadı" in page.text
    assert not page.docs
    assert not page.product_choices
    assert "stok" not in page.text.lower()
