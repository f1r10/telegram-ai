"""Small bounded evidence planner with deterministic fast paths."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from office_assistant.ai.provider import LanguageModel, ModelResponse
from office_assistant.internal_evidence.models import (
    EvidencePlan,
    PlannedToolCall,
    RoutingCategory,
)
from office_assistant.internal_evidence.query_normalization import normalize_user_query
from office_assistant.internal_evidence.tool_adapter import OpenAIToolSchemaAdapter

PLANNER_POLICY = """Classify the question and plan internal evidence collection.
Use only the supplied allowlisted read-only business tools. Tool arguments never
contain actor/user/role fields. Encode each tool's arguments_json as a valid JSON
object string matching its input schema. Internal company facts must not use general model
knowledge. Include document search for source wording, contracts, warranties,
proposal terms, provenance, or potentially differing document prices. Return only
concise structured planning metadata, never chain-of-thought."""


@dataclass(frozen=True, slots=True)
class PlanDecision:
    plan: EvidencePlan
    response: ModelResponse | None = None


class EvidencePlanner:
    def __init__(self, model: LanguageModel, adapter: OpenAIToolSchemaAdapter) -> None:
        self.model = model
        self.adapter = adapter

    async def plan(
        self, user_id: int, question: str, *, force_internal: bool = False
    ) -> PlanDecision:
        fast = deterministic_plan(question, force_internal=force_internal)
        if fast is not None:
            return PlanDecision(normalize_tool_plan(fast))
        normalized = normalize_user_query(question)
        response = await self.model.generate(
            instructions=PLANNER_POLICY,
            input_messages=[
                {
                    "role": "user",
                    "content": (
                        f"Original question: {question}\n"
                        f"Deterministic language hint: {normalized}\n\nAvailable tools:\n"
                        + json.dumps(self.adapter.schemas(), ensure_ascii=False)
                    ),
                }
            ],
            output_schema=EvidencePlan,
            request_type="internal_router",
            user_id=user_id,
        )
        if not isinstance(response.parsed, EvidencePlan):
            raise ValueError("Router returned an invalid plan")
        return PlanDecision(normalize_tool_plan(response.parsed), response)


def deterministic_plan(question: str, *, force_internal: bool = False) -> EvidencePlan | None:
    text = normalize_user_query(question)
    product = _product_code(question)
    if product and any(
        word in text for word in ("qiymet", "price", "цена", "fiyat", "neceye", "neceyedi")
    ):
        return EvidencePlan(
            category=RoutingCategory.INTERNAL_HYBRID,
            intent="PRODUCT_PRICE",
            use_structured_tools=True,
            use_document_search=True,
            business_tool_calls=[_tool_call("resolve_product_price", {"product": product})],
            reason_code="product_price_may_have_document_evidence",
        )
    # A catalog match proves the product exists in the approved catalog, not in stock.
    # Resolve the exact code first; documents are supporting evidence only.
    if product and any(
        phrase in text for phrase in ("varmi", "var?", "bizde var", "bizde yox", "stok", "elde var")
    ):
        return EvidencePlan(
            category=RoutingCategory.INTERNAL_HYBRID,
            intent="PRODUCT_LOOKUP",
            use_structured_tools=True,
            use_document_search=True,
            business_tool_calls=[_tool_call("find_product", {"code_or_alias": product})],
            reason_code="exact_catalog_identity_before_documents",
        )
    if product and any(word in text for word in ("zemanet", "sert", "sened", "muqavile")):
        return EvidencePlan(
            category=RoutingCategory.INTERNAL_DOCUMENT,
            intent="DOCUMENT_TERMS",
            use_structured_tools=False,
            use_document_search=True,
            reason_code="document_wording_requested",
        )
    if any(word in text for word in ("odenis", "odeyib", "odedi", "payment")):
        customer = _customer_prefix(question)
        arguments: dict[str, object] = {"customer": customer}
        if "bu ay" in text:
            now = datetime.now(UTC)
            arguments["from_date"] = datetime(now.year, now.month, 1, tzinfo=UTC).isoformat()
            arguments["to_date"] = now.isoformat()
        return EvidencePlan(
            category=RoutingCategory.INTERNAL_STRUCTURED,
            intent="PAYMENT_TOTALS",
            use_structured_tools=True,
            use_document_search=False,
            business_tool_calls=[_tool_call("get_customer_payment_totals", arguments)],
            reason_code="authoritative_payment_calculation",
        )
    if "teklif" in text or "proposal" in text:
        if text.startswith(("son 2 teklif", "son iki teklif")):
            return EvidencePlan(
                category=RoutingCategory.CLARIFICATION_REQUIRED,
                intent="PROPOSAL_COMPARISON",
                use_structured_tools=False,
                use_document_search=False,
                requires_clarification=True,
                reason_code="customer_required_for_proposal_comparison",
            )
        customer = _customer_prefix(question)
        compare = any(
            word in text for word in ("muqayise", "son iki", "son 2", "arasinda ferq", "compare")
        )
        document = any(word in text for word in ("sert", "sened", "muqavile"))
        return EvidencePlan(
            category=(
                RoutingCategory.INTERNAL_HYBRID if document else RoutingCategory.INTERNAL_STRUCTURED
            ),
            intent="PROPOSAL_COMPARISON" if compare else "LATEST_PROPOSAL",
            use_structured_tools=True,
            use_document_search=document,
            business_tool_calls=[
                PlannedToolCall(
                    tool_name=(
                        "get_customer_proposals" if compare else "get_latest_customer_proposal"
                    ),
                    arguments_json=json.dumps(
                        {"customer": customer, **({"limit": 2} if compare else {})},
                        ensure_ascii=False,
                    ),
                )
            ],
            reason_code="deterministic_proposal_query",
        )
    internal_markers = (
        "azn",
        "mmc",
        "bizim",
        "musteri",
        "menbe",
        "fayl",
        "sened",
        "mehsul",
        "bizde",
        "biz ",
        "satiriq",
        "neceye",
        "qiymet",
        "markali",
    )
    if force_internal:
        return None
    if not product and not any(item in text for item in internal_markers):
        return EvidencePlan(
            category=RoutingCategory.GENERAL_CHAT,
            intent="GENERAL_WORLD",
            use_structured_tools=False,
            use_document_search=False,
            allow_general_knowledge=True,
            reason_code="obvious_general_fast_path",
        )
    return None


def _product_code(question: str) -> str | None:
    match = re.search(
        r"\b([A-Za-z]{1,8}(?:[- ][A-Za-z]{1,8})?[- ]?\d{1,8}[A-Za-z0-9-]*)\b",
        question,
    )
    if match is None:
        return None
    candidate = match.group(1)
    possessive = re.fullmatch(r"(.+\d)-?(in|ın|un|ün)", candidate)
    return possessive.group(1) if possessive and candidate[-2:].islower() else candidate


def _tool_call(name: str, arguments: dict[str, object]) -> PlannedToolCall:
    return PlannedToolCall(
        tool_name=name,
        arguments_json=json.dumps(arguments, ensure_ascii=False),
    )


def normalize_tool_plan(plan: EvidencePlan) -> EvidencePlan:
    """Apply a few explicit read-only dependency and redundancy rules."""

    calls = list(plan.business_tool_calls)
    names = {item.tool_name for item in calls}
    if "resolve_product_price" in names:
        calls = [item for item in calls if item.tool_name != "find_product"]
    unique: list[PlannedToolCall] = []
    seen: set[tuple[str, str]] = set()
    for call in calls:
        key = (call.tool_name, call.arguments_json)
        if key not in seen:
            seen.add(key)
            unique.append(call)
    return plan.model_copy(update={"business_tool_calls": unique[:4]})


def _customer_prefix(question: str) -> str:
    text = question.strip()
    delimiter = re.search(
        r"\s+(?:bu\s+ay|üçün|ucun|son|nə\s+qədər|ne\s+qeder|ödə\w*|ode\w*|payment|təklif\w*|teklif\w*)\b",
        text,
        re.IGNORECASE,
    )
    customer = (text[: delimiter.start()] if delimiter else text).strip(" ,?\t")
    explicit = re.sub(r"(?:[-'’\s]+)(?:n?[ıiuü]n)$", "", customer, flags=re.IGNORECASE)
    if explicit != customer:
        return explicit
    compact = re.fullmatch(r"(.*\s)?([A-Za-z]{2,8}?)(?:nin|nın|nun|nün)", customer)
    return f"{compact.group(1) or ''}{compact.group(2)}".strip() if compact else customer
