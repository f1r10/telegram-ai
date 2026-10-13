"""Product-code lookup must be deterministic and never assume warehouse stock."""

import json

from office_assistant.internal_evidence.models import RoutingCategory
from office_assistant.internal_evidence.router import deterministic_plan


def test_v2_catalog_question_resolves_exact_identifier() -> None:
    plan = deterministic_plan("QA-KA45 bizdə varmı?")
    assert plan is not None
    assert plan.category == RoutingCategory.INTERNAL_HYBRID
    assert plan.intent == "PRODUCT_LOOKUP"
    assert plan.use_structured_tools
    assert plan.use_document_search
    assert len(plan.business_tool_calls) == 1
    tool = plan.business_tool_calls[0]
    assert tool.tool_name == "find_product"
    assert json.loads(tool.arguments_json) == {"code_or_alias": "QA-KA45"}


def test_v2_stock_question_does_not_claim_stock_from_catalog_only() -> None:
    plan = deterministic_plan("KA45 stokda varmı?", force_internal=True)
    assert plan is not None
    assert plan.intent == "PRODUCT_LOOKUP"
    assert plan.business_tool_calls[0].tool_name == "find_product"


def test_v2_price_lookup_remains_higher_priority() -> None:
    plan = deterministic_plan("KA45 qiyməti neçədir, stokda varmı?")
    assert plan is not None
    assert plan.intent == "PRODUCT_PRICE"
    assert plan.business_tool_calls[0].tool_name == "resolve_product_price"


def test_v2_possessive_suffix_does_not_corrupt_product_code() -> None:
    plan = deterministic_plan("KA45-in qiyməti nədir?")
    assert plan is not None
    assert plan.intent == "PRODUCT_PRICE"
    assert json.loads(plan.business_tool_calls[0].arguments_json) == {"product": "KA45"}
