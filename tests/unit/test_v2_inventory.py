"""Inventory must not confuse catalog existence, sales and known warehouse quantities."""

from decimal import Decimal

import pytest

from office_assistant.application.inventory import (
    InventoryValidationError,
    StockDraft,
    parse_stock_draft,
    parse_stock_request,
)


def test_stock_actions_require_explicit_prefix_and_document_reference() -> None:
    assert parse_stock_draft("KA45 stokda varmı?") is None
    assert parse_stock_draft("KA45 satıldı") is None
    draft = parse_stock_draft("Anbar əməliyyatı: KA-45 | Əsas | GİRİŞ | 10.250 | QEB-001")
    assert draft == StockDraft("KA45", "Əsas", "RECEIPT", Decimal("10.250"), "QEB-001")
    assert parse_stock_draft("Anbar əməliyyatı: KA45 | Əsas | AÇILIŞ | 0 | SAY-0") == (
        StockDraft("KA45", "Əsas", "OPENING", Decimal("0"), "SAY-0")
    )
    assert parse_stock_request("Stok: KA-45") == "KA45"
    assert parse_stock_request("Anbar qalığı: KA45") == "KA45"
    assert parse_stock_request("KA45 kataloqda varmı?") is None


@pytest.mark.parametrize(
    "text",
    [
        "Anbar əməliyyatı: KA45 | Əsas | ÇIXIŞ | 0 | F-1",
        "Anbar əməliyyatı: KA45 | Əsas | GİRİŞ | -1 | F-1",
        "Anbar əməliyyatı: KA45 | Əsas | GİRİŞ | NaN | F-1",
        "Anbar əməliyyatı: KA45 | Əsas | GİRİŞ | Infinity | F-1",
        "Anbar əməliyyatı: KA45 | Əsas | GİRİŞ | 1.0001 | F-1",
        "Anbar əməliyyatı: KA45 | Əsas | AÇILIŞ | -1 | F-1",
        "Anbar əməliyyatı: KA45 | Əsas | AÇILIŞ | 3 |",
        "Anbar əməliyyatı: KA45 | Əsas | DÜZƏLİŞ | 4 | F-1",
        "Anbar əməliyyatı: KA45 | Əsas | GİRİŞ | 1",
    ],
)
def test_invalid_stock_draft_is_rejected_without_creating_changes(text: str) -> None:
    with pytest.raises(InventoryValidationError):
        parse_stock_draft(text)


def test_unrelated_conversation_does_not_activate_stock_operations() -> None:
    assert parse_stock_request("stokda neçə KA45 var?") is None
    assert parse_stock_draft("Satış əlavə et: A MMC | 2026-10-01 | 1 | 5 | AZN") is None
    with pytest.raises(InventoryValidationError):
        parse_stock_request("Stok: ")
