"""V2 financial record parsing: no implied writes, no floating point and correct years."""

from datetime import date
from decimal import Decimal

import pytest

from office_assistant.application.sales_ledger import (
    SalesRequest,
    SaleValidationError,
    customer_key,
    parse_sale_draft,
    parse_sales_request,
)

TODAY = date(2026, 10, 14)


def test_explicit_sale_draft_only_and_decimal_exactness() -> None:
    draft = parse_sale_draft(
        "Satış əlavə et: A Şirkəti | 2026-10-01 | INV-0001 | 125.50 | azn",
        today=TODAY,
    )
    assert draft is not None
    assert customer_key(draft.customer_name) == "a şirkəti"
    assert draft.invoice_number == "INV-0001"
    assert draft.sold_on == date(2026, 10, 1)
    assert draft.amount == Decimal("125.50")
    assert draft.currency == "AZN"
    assert parse_sale_draft("KA45 50 AZN") is None
    assert parse_sale_draft("Bu il nə qədər satmışıq?") is None


@pytest.mark.parametrize(
    "line",
    [
        "Satış əlavə et: A Şirkəti | 2026-10-01 | X1 | 0 | AZN",
        "Satış əlavə et: A Şirkəti | 2026-10-01 | X1 | -5 | AZN",
        "Satış əlavə et: A Şirkəti | 2026-10-01 | X1 | NaN | AZN",
        "Satış əlavə et: A Şirkəti | 2026-10-01 | X1 | Infinity | AZN",
        "Satış əlavə et: A Şirkəti | 2026-10-01 | X1 | 12.345 | AZN",
        "Satış əlavə et: A Şirkəti | 2026-12-25 | X1 | 50 | AZN",
        "Satış əlavə et: A Şirkəti | 2026-10-01 | X1 | 50 | XYZ1",
        "Satış əlavə et: A Şirkəti | 2026-10-01 | X1 | 50",
        "Satış əlavə et: | 2026-10-01 | X1 | 50 | AZN",
        "Satış əlavə et: A Şirkəti | 2026-10-01 | | 50 | AZN",
    ],
)
def test_malformed_completed_sales_never_create_a_draft(line: str) -> None:
    with pytest.raises(SaleValidationError):
        parse_sale_draft(line, today=TODAY)


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("Bu il A şirkətinə nə qədər satmışıq?", SalesRequest("A şirkəti", 2026)),
        ("Bu il A şirkətinə nəqədər məhsul satdıq?", SalesRequest("A şirkəti", 2026)),
        ("2026-cı ildə A şirkətinə nə qədər satış etmişik?", SalesRequest("A şirkəti", 2026)),
        ("A şirkətinə bu il nə qədər satmışıq?", SalesRequest("A şirkəti", 2026)),
        ("A Şirkəti | 2026", SalesRequest("A Şirkəti", 2026)),
    ],
)
def test_azerbaijani_annual_sales_phrases(
    utterance: str, expected: SalesRequest
) -> None:
    assert parse_sales_request(utterance, today=TODAY) == expected


def test_unrelated_chat_cannot_be_misclassified_as_yearly_sales() -> None:
    for message in (
        "Mən sənə fayl göndərdim",
        "KA45-in qiyməti neçədir?",
        "A şirkəti bank köçürməsi nə qədər edib?",
        "Satış təklifini yaz",
    ):
        assert parse_sales_request(message, today=TODAY) is None
