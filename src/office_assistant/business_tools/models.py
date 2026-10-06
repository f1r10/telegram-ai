"""Typed inputs and display-neutral outputs for deterministic business tools."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolStatus(StrEnum):
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICT = "CONFLICT"
    INVALID_REQUEST = "INVALID_REQUEST"


class ToolExecutionContext(StrictModel):
    user_id: int
    role: Literal["ADMIN", "USER"]
    request_id: str = Field(min_length=1, max_length=255)


class BusinessSource(StrictModel):
    source_type: str
    filename: str | None = None
    file_id: int | None = None
    locator: dict[str, object] | None = None
    ingestion_session_id: int | None = None


def _empty_sources() -> list[BusinessSource]:
    return []


class ToolResult[T](StrictModel):
    status: ToolStatus
    data: T | None = None
    warnings: list[str] = Field(default_factory=list)
    sources: list[BusinessSource] = Field(default_factory=_empty_sources)


class Page[T](StrictModel):
    items: list[T]
    has_more: bool
    next_offset: int | None = None


class DateRangeLimitInput(StrictModel):
    from_date: datetime | None = None
    to_date: datetime | None = None
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0, le=100_000)

    @model_validator(mode="after")
    def valid_range(self) -> "DateRangeLimitInput":
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date must not be after to_date")
        return self


class FindProductInput(StrictModel):
    code_or_alias: str = Field(min_length=1, max_length=512)
    include_inactive: bool = False


class GetProductInput(StrictModel):
    product_id: UUID
    include_inactive: bool = False


class ProductView(StrictModel):
    product_id: UUID
    display_code: str
    canonical_code: str
    name: str
    description: str | None
    is_active: bool
    matched_by: Literal["ID", "CANONICAL_CODE", "ALIAS"]


def _empty_products() -> list[ProductView]:
    return []


class ProductLookupView(StrictModel):
    product: ProductView | None = None
    candidates: list[ProductView] = Field(default_factory=_empty_products)


class FindCustomerInput(StrictModel):
    name_or_alias: str = Field(min_length=1, max_length=512)
    include_inactive: bool = False


class GetCustomerInput(StrictModel):
    customer_id: UUID
    include_inactive: bool = False


class CustomerView(StrictModel):
    customer_id: UUID
    display_name: str
    normalized_name: str
    legal_name: str | None
    is_active: bool
    matched_by: Literal["ID", "CANONICAL_NAME", "ALIAS"]


def _empty_customers() -> list[CustomerView]:
    return []


class CustomerLookupView(StrictModel):
    customer: CustomerView | None = None
    candidates: list[CustomerView] = Field(default_factory=_empty_customers)


class ResolvePriceInput(StrictModel):
    product: str = Field(min_length=1, max_length=512)
    customer: str | None = Field(default=None, max_length=512)
    as_of: datetime | None = None
    allow_general_fallback: bool = False


class PriceHistoryInput(DateRangeLimitInput):
    product: str = Field(min_length=1, max_length=512)
    customer: str | None = Field(default=None, max_length=512)


class PriceView(StrictModel):
    price_id: UUID
    product: ProductView
    customer: CustomerView | None
    amount: Decimal
    currency: str
    effective_at: datetime
    valid_until: datetime | None
    price_kind: str
    used_general_fallback: bool = False
    source: BusinessSource


def _empty_prices() -> list[PriceView]:
    return []


class PriceResolutionView(StrictModel):
    price: PriceView | None = None
    conflicting_prices: list[PriceView] = Field(default_factory=_empty_prices)


class CustomerListInput(DateRangeLimitInput):
    customer: str = Field(min_length=1, max_length=512)


class PaymentHistoryInput(CustomerListInput):
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class PaymentTotalsInput(StrictModel):
    customer: str = Field(min_length=1, max_length=512)
    from_date: datetime | None = None
    to_date: datetime | None = None

    @model_validator(mode="after")
    def valid_range(self) -> "PaymentTotalsInput":
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date must not be after to_date")
        return self


class PaymentView(StrictModel):
    payment_id: UUID
    customer_id: UUID
    amount: Decimal
    currency: str
    paid_at: datetime
    description: str | None
    source: BusinessSource


class PaymentTotalsView(StrictModel):
    payment_count: int
    totals: dict[str, Decimal]


class EventHistoryInput(CustomerListInput):
    event_type: str | None = None


class EventView(StrictModel):
    event_id: UUID
    customer_id: UUID
    event_type: str
    occurred_at: datetime
    summary: str
    details: str | None
    source: BusinessSource


class ProposalHistoryInput(StrictModel):
    customer: str = Field(min_length=1, max_length=512)
    from_date: date | None = None
    to_date: date | None = None
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0, le=100_000)

    @model_validator(mode="after")
    def valid_range(self) -> "ProposalHistoryInput":
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date must not be after to_date")
        return self


class LatestProposalInput(StrictModel):
    customer: str = Field(min_length=1, max_length=512)


class ProposalDetailsInput(StrictModel):
    proposal_id: UUID


class ProposalSummary(StrictModel):
    proposal_id: UUID
    customer_id: UUID
    proposal_number: str | None
    proposal_date: date
    status: str
    notes: str | None
    created_at: datetime
    source: BusinessSource


class ProposalItemView(StrictModel):
    item_id: UUID
    position: int
    product_id: UUID | None
    product_code: str | None
    raw_product_name: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    currency: str | None
    line_total: Decimal | None
    source: BusinessSource


class ProposalDocumentView(StrictModel):
    file_id: int
    filename: str
    relation_type: str


class ProposalDetails(StrictModel):
    proposal: ProposalSummary
    items: list[ProposalItemView]
    documents: list[ProposalDocumentView]


class CompareProposalsInput(StrictModel):
    proposal_a: UUID
    proposal_b: UUID

    @model_validator(mode="after")
    def distinct(self) -> "CompareProposalsInput":
        if self.proposal_a == self.proposal_b:
            raise ValueError("proposal_a and proposal_b must be different")
        return self


class MatchedProposalItem(StrictModel):
    match_key: str
    item_a: ProposalItemView
    item_b: ProposalItemView
    quantity_delta: Decimal | None
    unit_price_delta: Decimal | None
    line_total_delta: Decimal | None
    currency_comparable: bool


class ProposalComparison(StrictModel):
    before_proposal: ProposalSummary
    after_proposal: ProposalSummary
    delta_direction: Literal["after_minus_before"] = "after_minus_before"
    proposal_a: ProposalSummary
    proposal_b: ProposalSummary
    matched_items: list[MatchedProposalItem]
    only_in_a: list[ProposalItemView]
    only_in_b: list[ProposalItemView]
    warnings: list[str]
