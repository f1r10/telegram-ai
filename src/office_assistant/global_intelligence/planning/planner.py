"""Deterministic-first planner (architecture §3.1, defects F1/F3/F4).

The shipped ``internal_evidence.router.deterministic_plan`` stays the authoritative fast path: when
it already produces an internal route, this planner wraps it unchanged. The new understanding layer
only *adds* routes for company questions the shipped router misroutes to general chat (F1) or
escalates to the LLM (F4). Every plan converts back to a deterministic ``EvidencePlan`` so the
engine can degrade to the shipped behaviour when a provider fails (promt.md §5). Only allow-listed
business tools are ever emitted; no free-form SQL and no side effects.
"""

from __future__ import annotations

import json

from office_assistant.global_intelligence.models import (
    QueryPlan,
    QueryUnderstanding,
    SourceType,
)
from office_assistant.global_intelligence.understanding.lexicon import DOMAIN_CONCEPTS
from office_assistant.internal_evidence.models import (
    EvidencePlan,
    PlannedToolCall,
    RoutingCategory,
)
from office_assistant.internal_evidence.router import deterministic_plan


def plan_query(question: str, understanding: QueryUnderstanding) -> QueryPlan:
    """Plan evidence collection for an understood question, deterministic fast path first."""

    shipped = deterministic_plan(question)
    if shipped is not None and shipped.category is not RoutingCategory.GENERAL_CHAT:
        return _from_shipped(shipped, understanding)
    if understanding.requires_clarification:
        return QueryPlan(
            category=RoutingCategory.CLARIFICATION_REQUIRED,
            intent="CLARIFY_AMBIGUOUS_TOKEN",
            sub_questions=understanding.sub_questions,
            requires_clarification=True,
            clarification_options=list(understanding.ambiguous_tokens),
            reason_code="gi_ambiguous_typo_correction",
        )
    domain = [concept for concept in understanding.concepts if concept in DOMAIN_CONCEPTS]
    if domain:
        return _internal_route(understanding, set(domain))
    if shipped is not None:
        return _from_shipped(shipped, understanding)
    return QueryPlan(
        category=RoutingCategory.GENERAL_CHAT,
        intent="GENERAL_WORLD",
        sub_questions=understanding.sub_questions,
        allow_general_knowledge=True,
        reason_code="gi_no_internal_signal",
    )


def _from_shipped(plan: EvidencePlan, understanding: QueryUnderstanding) -> QueryPlan:
    """Wrap an authoritative shipped plan, preserving its category, intent and tool calls."""

    sources: list[SourceType] = []
    if plan.use_structured_tools:
        sources.append(SourceType.POSTGRES)
    if plan.use_document_search:
        sources.append(SourceType.DOCUMENT)
    return QueryPlan(
        category=plan.category,
        intent=plan.intent,
        sub_questions=understanding.sub_questions,
        sources=sources,
        use_structured_tools=plan.use_structured_tools,
        use_document_search=plan.use_document_search,
        allow_general_knowledge=plan.allow_general_knowledge,
        business_tool_calls=plan.business_tool_calls,
        requires_clarification=plan.requires_clarification,
        reason_code=plan.reason_code,
    )


def _internal_route(understanding: QueryUnderstanding, concepts: set[str]) -> QueryPlan:
    """Route a recognized company question to internal sources by concept priority."""

    code = understanding.identifiers[0] if understanding.identifiers else None
    subs = understanding.sub_questions
    if "price" in concepts and code:
        return QueryPlan(
            category=RoutingCategory.INTERNAL_HYBRID,
            intent="PRODUCT_PRICE",
            sub_questions=subs,
            sources=[SourceType.POSTGRES, SourceType.DOCUMENT],
            use_structured_tools=True,
            use_document_search=True,
            business_tool_calls=[_tool("resolve_product_price", {"product": code})],
            reason_code="gi_price_concept",
        )
    if "stock" in concepts:
        return _product_route(
            understanding,
            code,
            intent="STOCK_QUANTITY",
            reason_code="gi_stock_concept",
        )
    if concepts & {"warranty", "contract"}:
        return QueryPlan(
            category=RoutingCategory.INTERNAL_DOCUMENT,
            intent="DOCUMENT_TERMS",
            sub_questions=subs,
            sources=[SourceType.DOCUMENT],
            use_document_search=True,
            reason_code="gi_terms_concept",
        )
    if "cable" in concepts:
        return _product_route(
            understanding,
            code,
            intent="ACCESSORY_COMPATIBILITY",
            reason_code="gi_accessory_concept",
        )
    if "supplier" in concepts:
        return QueryPlan(
            category=RoutingCategory.INTERNAL_DOCUMENT,
            intent="SUPPLIER_LOOKUP",
            sub_questions=subs,
            sources=[SourceType.DOCUMENT],
            use_document_search=True,
            reason_code="gi_supplier_concept",
        )
    if "order" in concepts:
        return QueryPlan(
            category=RoutingCategory.INTERNAL_STRUCTURED,
            intent="ORDER_HISTORY",
            sub_questions=subs,
            sources=[SourceType.POSTGRES],
            use_structured_tools=True,
            reason_code="gi_order_concept",
        )
    return _product_route(
        understanding,
        code,
        intent="PRODUCT_AVAILABILITY",
        reason_code="gi_domain_concept",
    )


def _product_route(
    understanding: QueryUnderstanding, code: str | None, *, intent: str, reason_code: str
) -> QueryPlan:
    """Resolve an exact catalog identity first when a code exists; documents support it.

    Without a machine code there is no Product row to resolve, so the route is document-only —
    the same honesty the shipped router applies to catalogue-name questions.
    """

    if code:
        return QueryPlan(
            category=RoutingCategory.INTERNAL_HYBRID,
            intent=intent,
            sub_questions=understanding.sub_questions,
            sources=[SourceType.POSTGRES, SourceType.DOCUMENT],
            use_structured_tools=True,
            use_document_search=True,
            business_tool_calls=[_tool("find_product", {"code_or_alias": code})],
            reason_code=reason_code,
        )
    return QueryPlan(
        category=RoutingCategory.INTERNAL_DOCUMENT,
        intent=intent,
        sub_questions=understanding.sub_questions,
        sources=[SourceType.DOCUMENT],
        use_document_search=True,
        reason_code=reason_code,
    )


def _tool(name: str, arguments: dict[str, object]) -> PlannedToolCall:
    return PlannedToolCall(tool_name=name, arguments_json=json.dumps(arguments, ensure_ascii=False))


__all__ = ["plan_query"]
