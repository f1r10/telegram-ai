"""Explicit allowlisted registry for provider-independent business tools."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol, cast

from pydantic import BaseModel, ValidationError

from office_assistant.business_tools.models import (
    CompareProposalsInput,
    CustomerLookupView,
    CustomerView,
    EventHistoryInput,
    EventView,
    FindCustomerInput,
    FindProductInput,
    GetCustomerInput,
    GetProductInput,
    LatestProposalInput,
    Page,
    PaymentHistoryInput,
    PaymentTotalsInput,
    PaymentTotalsView,
    PaymentView,
    PriceHistoryInput,
    PriceResolutionView,
    PriceView,
    ProductLookupView,
    ProductView,
    ProposalComparison,
    ProposalDetails,
    ProposalDetailsInput,
    ProposalHistoryInput,
    ProposalSummary,
    ResolvePriceInput,
    ToolExecutionContext,
    ToolResult,
    ToolStatus,
)
from office_assistant.business_tools.service import BusinessQueryService

logger = logging.getLogger(__name__)


class BusinessTool(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def input_model(self) -> type[BaseModel]: ...

    @property
    def output_model(self) -> type[BaseModel]: ...

    async def execute(
        self, context: ToolExecutionContext, payload: dict[str, object]
    ) -> BaseModel: ...


@dataclass(frozen=True)
class TypedBusinessTool[InputT: BaseModel, OutputT: BaseModel]:
    name: str
    description: str
    typed_input_model: type[InputT]
    typed_output_model: type[OutputT]
    executor: Callable[[ToolExecutionContext, InputT], Awaitable[OutputT]]

    @property
    def input_model(self) -> type[BaseModel]:
        return cast(type[BaseModel], self.typed_input_model)

    @property
    def output_model(self) -> type[BaseModel]:
        return cast(type[BaseModel], self.typed_output_model)

    async def execute(self, context: ToolExecutionContext, payload: dict[str, object]) -> BaseModel:
        value = self.typed_input_model.model_validate(payload)
        return await self.executor(context, value)


class BusinessToolRegistry:
    """The only public dispatch surface; registration is static and deliberate."""

    def __init__(self, service: BusinessQueryService) -> None:
        definitions: tuple[BusinessTool, ...] = (
            TypedBusinessTool(
                "find_product",
                "Find an authoritative product by canonical code or exact normalized alias.",
                FindProductInput,
                ToolResult[ProductLookupView],
                service.find_product,
            ),
            TypedBusinessTool(
                "get_product",
                "Get one authoritative product by its internal ID after prior resolution.",
                GetProductInput,
                ToolResult[ProductView],
                service.get_product,
            ),
            TypedBusinessTool(
                "find_customer",
                "Find an authoritative customer by normalized name or exact normalized alias.",
                FindCustomerInput,
                ToolResult[CustomerLookupView],
                service.find_customer,
            ),
            TypedBusinessTool(
                "get_customer",
                "Get one authoritative customer by its internal ID after prior resolution.",
                GetCustomerInput,
                ToolResult[CustomerView],
                service.get_customer,
            ),
            TypedBusinessTool(
                "resolve_product_price",
                "Resolve an authoritative price for a product, optional customer, and date.",
                ResolvePriceInput,
                ToolResult[PriceResolutionView],
                service.resolve_product_price,
            ),
            TypedBusinessTool(
                "get_product_price_history",
                "List bounded authoritative price history for one product and exact scope.",
                PriceHistoryInput,
                ToolResult[Page[PriceView]],
                service.get_product_price_history,
            ),
            TypedBusinessTool(
                "get_customer_payments",
                "List bounded active payments with optional date and currency filters.",
                PaymentHistoryInput,
                ToolResult[Page[PaymentView]],
                service.get_customer_payments,
            ),
            TypedBusinessTool(
                "get_customer_payment_totals",
                "Calculate authoritative customer payment totals grouped strictly by currency.",
                PaymentTotalsInput,
                ToolResult[PaymentTotalsView],
                service.get_customer_payment_totals,
            ),
            TypedBusinessTool(
                "get_customer_events",
                "List bounded customer events with explicit type and inclusive date filters.",
                EventHistoryInput,
                ToolResult[Page[EventView]],
                service.get_customer_events,
            ),
            TypedBusinessTool(
                "get_customer_proposals",
                "List bounded proposal metadata for one customer without expanding line items.",
                ProposalHistoryInput,
                ToolResult[Page[ProposalSummary]],
                service.get_customer_proposals,
            ),
            TypedBusinessTool(
                "get_latest_customer_proposal",
                "Return the deterministically latest active proposal for one customer.",
                LatestProposalInput,
                ToolResult[ProposalSummary],
                service.get_latest_customer_proposal,
            ),
            TypedBusinessTool(
                "get_proposal_details",
                "Return proposal metadata, items, linked files, and bounded provenance.",
                ProposalDetailsInput,
                ToolResult[ProposalDetails],
                service.get_proposal_details,
            ),
            TypedBusinessTool(
                "compare_proposals",
                "Compare two proposals deterministically without cross-currency subtraction.",
                CompareProposalsInput,
                ToolResult[ProposalComparison],
                service.compare_proposals,
            ),
        )
        self._tools = {tool.name: tool for tool in definitions}
        self._service = service

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    async def execute(
        self,
        name: str,
        context: ToolExecutionContext,
        payload: dict[str, object],
    ) -> BaseModel:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult[object](
                status=ToolStatus.INVALID_REQUEST, warnings=["tool_not_allowlisted"]
            )
        started = perf_counter()
        try:
            await self._service.authorize(context)
            result = await tool.execute(context, payload)
        except ValidationError as error:
            result = ToolResult[object](
                status=ToolStatus.INVALID_REQUEST,
                warnings=[item["type"] for item in error.errors(include_url=False)],
            )
        elapsed_ms = round((perf_counter() - started) * 1000)
        status = getattr(result, "status", "UNKNOWN")
        count = _result_count(result)
        logger.info(
            "Business tool executed | tool=%s actor_user_id=%s request_id=%s status=%s "
            "latency_ms=%s result_count=%s",
            name,
            context.user_id,
            context.request_id,
            status,
            elapsed_ms,
            count,
        )
        return result

    def schemas(self) -> list[dict[str, object]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_model.model_json_schema(),
                "output_schema": tool.output_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]


def _result_count(result: BaseModel) -> int:
    data = getattr(result, "data", None)
    if data is None:
        return 0
    items = getattr(data, "items", None)
    return len(cast(list[object], items)) if isinstance(items, list) else 1
