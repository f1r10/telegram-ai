"""Availability questions must search the saved documents, not only the product table."""

import json

from office_assistant.internal_evidence.models import (
    EvidencePlan,
    RoutingCategory,
    StructuredEvidence,
)
from office_assistant.internal_evidence.router import deterministic_plan
from office_assistant.internal_evidence.service import document_search_after_empty_structured


def _plan(question: str) -> EvidencePlan | None:
    return deterministic_plan(question, force_internal=True)


def _documents_and_tool(question: str, tool: str) -> dict[str, object]:
    plan = _plan(question)
    assert plan is not None
    assert plan.use_document_search
    assert plan.intent == "PRODUCT_LOOKUP"
    assert len(plan.business_tool_calls) == 1
    assert plan.business_tool_calls[0].tool_name == tool
    return json.loads(plan.business_tool_calls[0].arguments_json)


def test_availability_after_the_verb_still_resolves_the_product() -> None:
    assert _documents_and_tool("QA-KA45 bizdə varmı?", "find_product") == {
        "code_or_alias": "QA-KA45"
    }


def test_availability_before_the_verb_resolves_the_product() -> None:
    # The owner writes «var bizdə»; a "bizdə var" substring test missed it and the
    # question fell to a model planner that never opened the catalogue.
    assert _documents_and_tool("Konus 75 sm var bizdə?", "find_product") == {
        "code_or_alias": "Konus 75"
    }


def test_stock_wording_keeps_document_evidence() -> None:
    plan = _plan("KA45 stokda varmı?")
    assert plan is not None
    assert plan.use_document_search
    assert plan.business_tool_calls[0].tool_name == "find_product"


def test_negative_existence_question_searches_documents() -> None:
    plan = _plan("KA45 bizdə yoxdur?")
    assert plan is not None
    assert plan.use_document_search


def test_price_question_keeps_priority_over_availability() -> None:
    plan = _plan("KA45 qiyməti neçədir, stokda varmı?")
    assert plan is not None
    assert plan.intent == "PRODUCT_PRICE"
    assert plan.business_tool_calls[0].tool_name == "resolve_product_price"


def test_small_talk_is_not_routed_as_an_availability_lookup() -> None:
    assert _plan("Salam, necəsən?") is None


def test_empty_structured_evidence_searches_documents_once() -> None:
    plan = EvidencePlan(
        category=RoutingCategory.INTERNAL_STRUCTURED,
        intent="PRODUCT_LOOKUP",
        use_structured_tools=True,
        use_document_search=False,
        reason_code="model_planner_chose_tools_only",
    )
    assert document_search_after_empty_structured(
        plan, [StructuredEvidence(tool_name="find_product", status="NOT_FOUND")]
    )


def test_successful_structured_evidence_does_not_add_a_retrieval() -> None:
    plan = EvidencePlan(
        category=RoutingCategory.INTERNAL_HYBRID,
        intent="PRODUCT_LOOKUP",
        use_structured_tools=True,
        use_document_search=False,
        reason_code="model_planner_chose_tools_only",
    )
    assert not document_search_after_empty_structured(
        plan, [StructuredEvidence(tool_name="find_product", status="OK", data={"code": "KA45"})]
    )


def test_plan_that_already_asked_for_documents_is_not_rechecked() -> None:
    plan = EvidencePlan(
        category=RoutingCategory.INTERNAL_DOCUMENT,
        intent="DOCUMENT_TERMS",
        use_structured_tools=False,
        use_document_search=True,
        reason_code="document_wording_requested",
    )
    assert not document_search_after_empty_structured(
        plan, [StructuredEvidence(tool_name="find_product", status="NOT_FOUND")]
    )


def test_turn_without_any_business_record_does_not_widen_the_search() -> None:
    plan = EvidencePlan(
        category=RoutingCategory.INTERNAL_DOCUMENT,
        intent="ATTACHED_IMAGE_LABEL_READ",
        use_structured_tools=False,
        use_document_search=False,
        reason_code="explicit_attached_image_evidence",
    )
    assert not document_search_after_empty_structured(plan, [])


def test_general_chat_plan_never_triggers_the_document_fallback() -> None:
    plan = EvidencePlan(
        category=RoutingCategory.GENERAL_CHAT,
        intent="GENERAL_WORLD",
        use_structured_tools=False,
        use_document_search=False,
        allow_general_knowledge=True,
        reason_code="obvious_general_fast_path",
    )
    assert not document_search_after_empty_structured(
        plan, [StructuredEvidence(tool_name="find_product", status="NOT_FOUND")]
    )
