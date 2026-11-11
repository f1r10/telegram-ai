"""Phase 1 — deterministic-first planner (defects F1/F3/F4)."""

from __future__ import annotations

import pytest

from office_assistant.global_intelligence.models import QueryPlan, SourceType
from office_assistant.global_intelligence.planning.planner import plan_query
from office_assistant.global_intelligence.understanding import understand_query
from office_assistant.internal_evidence.models import EvidencePlan, RoutingCategory
from office_assistant.internal_evidence.router import deterministic_plan

# Mirrors business_tools/registry.py: the planner may only ever emit these allow-listed tools.
_ALLOWLISTED_TOOLS = frozenset(
    {
        "find_product",
        "get_product",
        "find_customer",
        "get_customer",
        "resolve_product_price",
        "get_product_price_history",
        "get_customer_payments",
        "get_customer_payment_totals",
        "get_customer_events",
        "get_customer_proposals",
        "get_latest_customer_proposal",
        "get_proposal_details",
        "compare_proposals",
    }
)


def _plan(question: str) -> QueryPlan:
    return plan_query(question, understand_query(question))


def test_company_question_no_longer_misroutes_to_general_chat() -> None:
    # Defect F1: the shipped router sends both of these to GENERAL_CHAT.
    for question in ("Plinter üçün kabel var?", "Kamera üçün hansı kabellər var?"):
        shipped = deterministic_plan(question)
        assert shipped is not None
        assert shipped.category is RoutingCategory.GENERAL_CHAT
        plan = _plan(question)
        assert plan.category is RoutingCategory.INTERNAL_DOCUMENT
        assert plan.intent == "ACCESSORY_COMPATIBILITY"
        assert plan.allow_general_knowledge is False
        assert plan.use_document_search is True


def test_code_present_resolves_catalog_identity_first() -> None:
    plan = _plan("Plinter M404 üçün kabel lazımdır")
    assert plan.category is RoutingCategory.INTERNAL_HYBRID
    assert plan.sources == [SourceType.POSTGRES, SourceType.DOCUMENT]
    assert plan.business_tool_calls[0].tool_name == "find_product"
    assert plan.business_tool_calls[0].parsed_arguments() == {"code_or_alias": "M404"}


def test_shipped_internal_routes_are_preserved_unchanged() -> None:
    price = _plan("M404 printerinin qiyməti nədir?")
    assert price.intent == "PRODUCT_PRICE"
    assert price.business_tool_calls[0].tool_name == "resolve_product_price"
    terms = _plan("M404 printerinin zəmanəti neçə ildir?")
    assert terms.category is RoutingCategory.INTERNAL_DOCUMENT
    assert terms.intent == "DOCUMENT_TERMS"


def test_stock_question_routes_to_structured_and_document() -> None:
    plan = _plan("M404dən anbarda neçə ədəd qalıb?")
    assert plan.intent == "STOCK_QUANTITY"
    assert plan.category is RoutingCategory.INTERNAL_HYBRID
    assert SourceType.POSTGRES in plan.sources
    assert plan.business_tool_calls[0].parsed_arguments() == {"code_or_alias": "M404"}


def test_small_talk_stays_general_chat() -> None:
    plan = _plan("Salam, necəsən?")
    assert plan.category is RoutingCategory.GENERAL_CHAT
    assert plan.allow_general_knowledge is True


def test_ambiguous_token_routes_to_clarification() -> None:
    plan = _plan("kable üçün")
    assert plan.category is RoutingCategory.CLARIFICATION_REQUIRED
    assert plan.requires_clarification is True
    assert "kable" in plan.clarification_options


def test_plan_degrades_to_a_deterministic_evidence_plan() -> None:
    plan = _plan("Plinter M404 üçün kabel lazımdır")
    evidence = plan.to_evidence_plan()
    assert isinstance(evidence, EvidencePlan)
    assert evidence.category is plan.category
    assert evidence.intent == plan.intent
    assert evidence.business_tool_calls == plan.business_tool_calls


@pytest.mark.parametrize(
    "question",
    [
        "Plinter üçün kabel var?",
        "Kamera üçün hansı kabellər var?",
        "Plinter M404 üçün kabel lazımdır",
        "HP M404 printerinə hansı kabel uyğundur?",
        "M404 printerinin qiyməti nədir?",
        "M404dən anbarda neçə ədəd qalıb?",
        "Salam, necəsən?",
    ],
)
def test_only_allowlisted_tools_are_emitted(question: str) -> None:
    plan = _plan(question)
    for call in plan.business_tool_calls:
        assert call.tool_name in _ALLOWLISTED_TOOLS, call.tool_name
