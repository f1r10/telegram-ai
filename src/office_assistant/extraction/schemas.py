"""Strict OpenAI-compatible extraction schemas; unknown fields are rejected."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CandidateType = Literal[
    "PRODUCT",
    "PRODUCT_ALIAS",
    "CUSTOMER",
    "CUSTOMER_ALIAS",
    "PRICE",
    "PAYMENT",
    "BUSINESS_EVENT",
    "PROPOSAL",
    "PROPOSAL_ITEM",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedCandidate(_Strict):
    """One flat envelope avoids JSON Schema ``oneOf``, unsupported by OpenAI."""

    candidate_type: CandidateType
    source_refs: list[str] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    group_key: str | None = None

    local_ref: str | None = None
    product_code: str | None = None
    name: str | None = None
    description: str | None = None
    product_ref: str | None = None
    alias: str | None = None
    display_name: str | None = None
    legal_name: str | None = None
    customer_ref: str | None = None
    amount: str | None = None
    currency: str | None = None
    effective_at: str | None = None
    price_kind: Literal["GENERAL", "CUSTOMER_SPECIFIC"] | None = None
    paid_at: str | None = None
    event_type: (
        Literal[
            "MEETING",
            "PHONE_CALL",
            "SERVICE_ACTIVITY",
            "DELIVERY",
            "NOTE",
            "AGREEMENT",
            "FOLLOW_UP",
            "OTHER",
        ]
        | None
    ) = None
    occurred_at: str | None = None
    summary: str | None = None
    details: str | None = None
    proposal_number: str | None = None
    proposal_date: str | None = None
    status: Literal["DRAFT", "SENT", "ACCEPTED", "REJECTED", "EXPIRED", "CANCELLED"] | None = None
    notes: str | None = None
    proposal_ref: str | None = None
    position: int | None = Field(default=None, gt=0)
    raw_product_name: str | None = None
    quantity: str | None = None
    unit_price: str | None = None
    line_total: str | None = None

    @model_validator(mode="after")
    def require_type_specific_fields(self) -> "ExtractedCandidate":
        required: dict[str, tuple[str, ...]] = {
            "PRODUCT": ("local_ref", "product_code", "name"),
            "PRODUCT_ALIAS": ("product_ref", "alias"),
            "CUSTOMER": ("local_ref", "display_name"),
            "CUSTOMER_ALIAS": ("customer_ref", "alias"),
            "PRICE": ("product_ref", "amount", "currency", "price_kind"),
            "PAYMENT": ("customer_ref", "amount", "currency"),
            "BUSINESS_EVENT": ("customer_ref", "event_type", "summary"),
            "PROPOSAL": ("local_ref", "customer_ref", "status"),
            "PROPOSAL_ITEM": ("proposal_ref", "position"),
        }
        missing = [field for field in required[self.candidate_type] if getattr(self, field) is None]
        if self.candidate_type == "PROPOSAL_ITEM" and not (
            self.product_ref or self.raw_product_name
        ):
            missing.append("product_ref or raw_product_name")
        if missing:
            raise ValueError(f"{self.candidate_type} missing required fields: {', '.join(missing)}")
        return self


class StructuredExtractionResult(_Strict):
    candidates: list[ExtractedCandidate]
    warnings: list[str] = Field(default_factory=list)
    unprocessed_sources: list[str] = Field(default_factory=list)
