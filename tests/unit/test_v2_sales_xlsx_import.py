"""Explicit reviewed sales spreadsheets must be parsed without guessing invoice values."""

from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook

from office_assistant.application.sales_import import parse_sales_xlsx
from office_assistant.application.sales_ledger import SaleValidationError


def workbook_bytes(rows: list[list[object]]) -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


HEADINGS: list[object] = ["Müştəri", "Tarix", "Faktura", "Məbləğ", "Valyuta"]


def test_two_invoices_are_proposed_without_database_writes() -> None:
    raw = workbook_bytes(
        [
            HEADINGS,
            ["A Şirkəti", date(2026, 10, 1), "INV-A1", 50, "azn"],
            ["A Şirkəti", "2026-10-02", "INV-A2", 75.25, "AZN"],
        ]
    )
    drafts = parse_sales_xlsx(raw, today=date(2026, 10, 15))
    assert len(drafts) == 2
    assert [d.amount for d in drafts] == [Decimal("50"), Decimal("75.25")]
    assert [d.invoice_number for d in drafts] == ["INV-A1", "INV-A2"]


@pytest.mark.parametrize(
    "row",
    [
        ["A Şirkəti", "2026-10-01", "INV-1", "=10*100", "AZN"],
        ["A Şirkəti", "2026-10-01", "INV-1", -1, "AZN"],
        ["A Şirkəti", "2026-10-01", "INV-1", "NaN", "AZN"],
        ["A Şirkəti", "2026-10-01", "INV-1", 12.345, "AZN"],
        ["A Şirkəti", "2026-11-01", "INV-1", 20, "AZN"],
        ["A Şirkəti", "2026-10-01", 123, 20, "AZN"],
    ],
)
def test_invalid_sale_rejects_entire_workbook(row: list[object]) -> None:
    with pytest.raises(SaleValidationError):
        parse_sales_xlsx(workbook_bytes([HEADINGS, row]), today=date(2026, 10, 15))


def test_duplicate_invoices_fail_before_confirmation() -> None:
    raw = workbook_bytes(
        [
            HEADINGS,
            ["A Şirkəti", "2026-10-01", "INV-1", 10, "AZN"],
            ["a şirkəti", "2026-10-02", "INV-1", 25, "AZN"],
        ]
    )
    with pytest.raises(SaleValidationError, match="təkrar"):
        parse_sales_xlsx(raw, today=date(2026, 10, 15))


def test_only_verified_header_fields_are_accepted() -> None:
    raw = workbook_bytes(
        [["Name", "Date", "Document", "Value", "Currency"], ["A", "2026-10-01", "INV-1", 55, "AZN"]]
    )
    with pytest.raises(SaleValidationError, match="Başlıq"):
        parse_sales_xlsx(raw, today=date(2026, 10, 15))


def test_large_or_non_xlsx_content_rejected() -> None:
    with pytest.raises(SaleValidationError):
        parse_sales_xlsx(b"", today=date(2026, 10, 15))
    with pytest.raises(SaleValidationError):
        parse_sales_xlsx(b"not an excel file", today=date(2026, 10, 15))
