"""Stock XLSX previews are bounded, formula-free and SHA-confirmed."""

from io import BytesIO

import pytest
from openpyxl import Workbook

from office_assistant.application.inventory import InventoryValidationError
from office_assistant.application.inventory_xlsx import (
    approved_drafts,
    preview_stock_xlsx,
)

HEADERS = ("product_code", "warehouse", "kind", "quantity", "reference")


def workbook(
    *rows: tuple[str | int | float | None, ...], headers: tuple[str, ...] = HEADERS
) -> bytes:
    document = Workbook()
    sheet = document.active
    assert sheet is not None
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    result = BytesIO()
    document.save(result)
    document.close()
    return result.getvalue()


def test_review_is_read_only_deterministic_and_requires_exact_sha() -> None:
    payload = workbook(
        ("KA-45", "Əsas", "AÇILIŞ", 3, "OPEN-1"),
        ("KA45", "Əsas", "GİRİŞ", 2.5, "IN-1"),
        ("KA45", "Əsas", "ÇIXIŞ", 1, "OUT-1"),
    )
    report = preview_stock_xlsx(payload)
    assert report.count == 3
    assert report.opening_count == 1
    assert report.receipt_count == 1
    assert report.issue_count == 1
    assert report.safe_summary()["rows"] == 3
    assert "KA45" not in str(report.safe_summary())
    assert approved_drafts(payload, confirmed_sha256=report.sha256) == report.drafts
    with pytest.raises(InventoryValidationError, match="dəyişib"):
        approved_drafts(payload, confirmed_sha256="0" * 64)


def test_reject_formula_even_when_other_cells_are_valid() -> None:
    payload = workbook(("KA45", "Əsas", "GİRİŞ", "=2+3", "IN-1"))
    with pytest.raises(InventoryValidationError, match="düstur"):
        preview_stock_xlsx(payload)


def test_reject_missing_header_duplicate_document_and_blank_row() -> None:
    with pytest.raises(InventoryValidationError, match="başlıqları"):
        preview_stock_xlsx(
            workbook(("KA45", "Əsas", "GİRİŞ", 1, "IN-1"), headers=("code", *HEADERS[1:]))
        )
    same = ("KA45", "Əsas", "GİRİŞ", 1, "IN-1")
    with pytest.raises(InventoryValidationError, match="təkrarlanır"):
        preview_stock_xlsx(workbook(same, same))
    with pytest.raises(InventoryValidationError, match="boş"):
        preview_stock_xlsx(workbook(("KA45", "Əsas", "GİRİŞ", 1, "IN-1"), (None,) * 5, same))


def test_reject_overlong_and_invalid_quantity_workbooks() -> None:
    with pytest.raises(InventoryValidationError):
        preview_stock_xlsx(b"bad zip")
    with pytest.raises(InventoryValidationError):
        preview_stock_xlsx(workbook(*[("KA45", "Əsas", "GİRİŞ", 1, f"IN-{i}") for i in range(201)]))
    with pytest.raises(InventoryValidationError):
        preview_stock_xlsx(workbook(("KA45", "Əsas", "GİRİŞ", -1, "IN-1")))
    with pytest.raises(InventoryValidationError):
        preview_stock_xlsx(workbook(("KA45", "Əsas", "GİRİŞ", 0.00001, "IN-1")))
