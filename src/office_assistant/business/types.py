"""Typed service inputs and deterministic query results."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from office_assistant.infrastructure.database.models import ProductPrice, SourceType


@dataclass(frozen=True)
class ProvenanceInput:
    source_type: SourceType
    file_id: int | None = None
    ingestion_session_id: int | None = None
    ingestion_session_item_id: int | None = None
    document_parse_run_id: int | None = None
    locator: dict[str, object] | None = None
    description: str | None = None


class PriceResolutionStatus(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class ResolvedPrice:
    status: PriceResolutionStatus
    price: ProductPrice | None = None
    conflicting_prices: tuple[ProductPrice, ...] = ()


@dataclass(frozen=True)
class ProposalItemInput:
    position: int
    provenance: ProvenanceInput
    product_id: UUID | None = None
    raw_product_name: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    currency: str | None = None
    line_total: Decimal | None = None
