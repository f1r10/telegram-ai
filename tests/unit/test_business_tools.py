from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from office_assistant.business_tools.models import (
    BusinessSource,
    PaymentHistoryInput,
    ProductView,
    ProposalDetails,
    ProposalItemView,
    ProposalSummary,
)
from office_assistant.business_tools.service import compare_proposal_details


def _proposal(number: str, items: list[ProposalItemView]) -> ProposalDetails:
    source = BusinessSource(source_type="TELEGRAM_USER")
    return ProposalDetails(
        proposal=ProposalSummary(
            proposal_id=uuid4(),
            customer_id=uuid4(),
            proposal_number=number,
            proposal_date=date(2026, 1, 1),
            status="SENT",
            notes=None,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            source=source,
        ),
        items=items,
        documents=[],
    )


def _item(
    position: int,
    *,
    product_id: UUID | None = None,
    raw: str | None = None,
    quantity: str = "1",
    price: str = "10",
    currency: str = "AZN",
) -> ProposalItemView:
    return ProposalItemView(
        item_id=uuid4(),
        position=position,
        product_id=product_id,
        product_code="KA45" if product_id else None,
        raw_product_name=raw,
        quantity=Decimal(quantity),
        unit_price=Decimal(price),
        currency=currency,
        line_total=Decimal(quantity) * Decimal(price),
        source=BusinessSource(source_type="TELEGRAM_USER"),
    )


def test_proposal_comparison_uses_decimal_and_never_subtracts_currencies() -> None:
    product_id = uuid4()
    first = _proposal(
        "A",
        [
            _item(1, product_id=product_id, quantity="2", price="10", currency="AZN"),
            _item(2, raw="Only A"),
        ],
    )
    second = _proposal(
        "B",
        [
            _item(1, product_id=product_id, quantity="3", price="12.5", currency="USD"),
            _item(2, raw="Only B"),
        ],
    )
    result = compare_proposal_details(first, second)
    assert result.before_proposal.proposal_number == "A"
    assert result.after_proposal.proposal_number == "B"
    assert result.delta_direction == "after_minus_before"
    assert result.matched_items[0].quantity_delta == Decimal("1")
    assert result.matched_items[0].unit_price_delta is None
    assert not result.matched_items[0].currency_comparable
    assert [item.raw_product_name for item in result.only_in_a] == ["Only A"]
    assert [item.raw_product_name for item in result.only_in_b] == ["Only B"]
    assert result.warnings


def test_duplicate_comparison_identity_remains_unmatched() -> None:
    product_id = uuid4()
    first = _proposal("A", [_item(1, product_id=product_id), _item(2, product_id=product_id)])
    second = _proposal("B", [_item(1, product_id=product_id)])
    result = compare_proposal_details(first, second)
    assert not result.matched_items
    assert len(result.only_in_a) == 2
    assert len(result.only_in_b) == 1
    assert result.warnings == [f"ambiguous_item_key:product:{product_id}"]


def test_tool_inputs_reject_unknown_fields_and_oversized_limits() -> None:
    with pytest.raises(ValidationError):
        PaymentHistoryInput.model_validate({"customer": "ABC", "limit": 101})
    with pytest.raises(ValidationError):
        PaymentHistoryInput.model_validate({"customer": "ABC", "sql": "DROP TABLE users"})


def test_product_view_contains_no_orm_or_mutation_surface() -> None:
    schema = ProductView.model_json_schema()
    assert "source_reference_id" not in schema.get("properties", {})
    assert "delete" not in schema.get("properties", {})
