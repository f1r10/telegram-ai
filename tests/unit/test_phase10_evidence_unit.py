import json
from types import SimpleNamespace
from typing import cast

from office_assistant.ai.schemas import GroundedModelOutput
from office_assistant.business_tools.models import BusinessSource
from office_assistant.business_tools.registry import BusinessToolRegistry
from office_assistant.internal_evidence.benchmark import (
    run_synthetic_routing_benchmark,
)
from office_assistant.internal_evidence.delivery import parse_file_delivery_request
from office_assistant.internal_evidence.models import (
    DocumentEvidence,
    EvidenceBundle,
    EvidencePlan,
    PlannedToolCall,
    RoutingCategory,
    StructuredEvidence,
)
from office_assistant.internal_evidence.router import deterministic_plan
from office_assistant.internal_evidence.service import (
    _bundle_prompt,  # pyright: ignore[reportPrivateUsage]
    _validated_synthesis,  # pyright: ignore[reportPrivateUsage]
    build_evidence_bundle,
)
from office_assistant.internal_evidence.tool_adapter import OpenAIToolSchemaAdapter


def _registry_stub() -> BusinessToolRegistry:
    schemas = [
        {
            "name": f"tool_{index}",
            "description": f"Tool {index}",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "additionalProperties": False,
            },
            "output_schema": {"type": "object"},
        }
        for index in range(13)
    ]
    return cast(BusinessToolRegistry, SimpleNamespace(schemas=lambda: schemas))


def test_tool_adapter_exposes_only_strict_registry_schemas() -> None:
    schemas = OpenAIToolSchemaAdapter(_registry_stub()).schemas()
    assert len(schemas) == 13
    assert all(item["type"] == "function" and item["strict"] is True for item in schemas)
    assert all("output_schema" not in item for item in schemas)
    parameters = cast(dict[str, object], schemas[0]["parameters"])
    assert parameters["additionalProperties"] is False
    assert "actor" not in str(schemas).casefold()
    assert "user_id" not in str(schemas).casefold()


def test_deterministic_routing_selects_required_channels() -> None:
    expected = {
        "ABC MMC bu ay nə qədər ödəniş edib?": RoutingCategory.INTERNAL_STRUCTURED,
        "KA45 zəmanət şərti nədir?": RoutingCategory.INTERNAL_DOCUMENT,
        "KA45-in qiyməti nədir?": RoutingCategory.INTERNAL_HYBRID,
        "ABC MMC təklif şərtlərini müqayisə et": RoutingCategory.INTERNAL_HYBRID,
        "Azərbaycanın paytaxtı haradır?": RoutingCategory.GENERAL_CHAT,
    }
    for question, category in expected.items():
        plan = deterministic_plan(question)
        assert plan is not None
        assert plan.category == category
        assert len(plan.business_tool_calls) <= 4


def test_proposal_route_strips_azerbaijani_genitive_suffix_from_customer() -> None:
    plan = deterministic_plan("ABC MMC-nin son iki təklifini müqayisə et.")

    assert plan is not None
    assert plan.intent == "PROPOSAL_COMPARISON"
    assert plan.business_tool_calls[0].parsed_arguments() == {
        "customer": "ABC MMC",
        "limit": 2,
    }


def test_planner_schema_is_openai_strict_and_arguments_are_validated() -> None:
    schema_text = str(EvidencePlan.model_json_schema())
    assert "additionalProperties': True" not in schema_text
    call = PlannedToolCall(tool_name="find_product", arguments_json='{"code_or_alias":"KA45"}')
    assert call.parsed_arguments() == {"code_or_alias": "KA45"}
    invalid = PlannedToolCall(tool_name="find_product", arguments_json="[]")
    try:
        invalid.parsed_arguments()
    except ValueError as error:
        assert "JSON object" in str(error)
    else:
        raise AssertionError("Non-object tool arguments must be rejected")


def test_unified_bundle_surfaces_125_vs_110_and_rejects_fabricated_citation() -> None:
    structured = StructuredEvidence(
        tool_name="resolve_product_price",
        status="OK",
        data={"price": {"amount": "125.0000", "currency": "AZN"}},
        sources=[
            BusinessSource(
                source_type="FILE", file_id=10, filename="price.xlsx", locator={"row": 2}
            )
        ],
    )
    document = DocumentEvidence(
        chunk_id="chunk-1",
        text="KA45 qiyməti 110 AZN-dir.",
        file_id=11,
        filename="catalog.pdf",
        locator={"page": 1},
        retrieval_method="hybrid",
        score=0.9,
    )
    bundle = build_evidence_bundle([structured], [document])
    assert [item.source_class for item in bundle.citations] == [
        "STRUCTURED_AUTHORITATIVE",
        "INTERNAL_DOCUMENT",
    ]
    assert [item.conflict_type for item in bundle.conflicts] == ["STRUCTURED_VS_DOCUMENT"]
    output = GroundedModelOutput(
        answer=("Biznes qiyməti 125 AZN-dir [E1], sənəddə 110 AZN yazılıb [E2] [E99] [X4] [99]."),
        citations=["E1", "E2", "E99"],
    )
    answer, citations = _validated_synthesis(output, bundle.citations, bundle.conflicts)
    assert "E99" not in answer and "X4" not in answer and "[99]" not in answer
    assert "[1]" in answer and "[2]" in answer
    assert "fərq" in answer
    assert [item.evidence_id for item in citations] == ["E1", "E2"]


def test_structured_and_document_conflict_states_are_preserved() -> None:
    structured = StructuredEvidence(
        tool_name="resolve_product_price",
        status="CONFLICT",
        data={"conflicting_prices": [{"amount": "125"}, {"amount": "126"}]},
    )
    documents = [
        DocumentEvidence(
            chunk_id=str(index),
            text=f"Qiymət {amount} AZN",
            file_id=index,
            filename=f"{index}.pdf",
            locator={"page": 1},
            retrieval_method="hybrid",
            score=1.0,
        )
        for index, amount in ((1, 110), (2, 115))
    ]
    bundle = build_evidence_bundle([structured], documents)
    kinds = {item.conflict_type for item in bundle.conflicts}
    assert {"STRUCTURED_INTERNAL", "STRUCTURED_VS_DOCUMENT", "DOCUMENT_VS_DOCUMENT"} <= kinds
    assert bundle.structured_results[0].status == "CONFLICT"


def test_natural_file_delivery_requests_are_deterministic() -> None:
    assert parse_file_delivery_request("2-ci mənbəni göndər") == 2
    assert parse_file_delivery_request("1-ci faylı göndər") == 1
    assert parse_file_delivery_request("PDF-i göndər") is None
    assert parse_file_delivery_request("onu göndər") is None
    assert parse_file_delivery_request("salam") is False


def test_phase10_benchmark_contract_scores_all_required_metrics() -> None:
    metrics = run_synthetic_routing_benchmark()
    assert metrics.cases == 9
    assert metrics.correct_channel_rate == 1.0
    assert metrics.missing_required_channel_rate == 0.0
    assert metrics.unnecessary_internal_call_rate == 0.0
    assert metrics.citation_validity_rate == 1.0
    assert metrics.conflict_surfacing_rate == 1.0
    assert metrics.no_source_hallucination_rate == 0.0
    assert metrics.file_reference_resolution_rate == 1.0


def _document_bundle() -> EvidenceBundle:
    structured = StructuredEvidence(
        tool_name="resolve_product_price",
        status="OK",
        data={"price": {"amount": "125.0000", "currency": "AZN"}},
        sources=[
            BusinessSource(
                source_type="TOOL",
                filename="Price list Diller.xlsx",
                file_id=10,
                locator={"blocks": [{"sheet": "Sheet1", "range": "A30:C30"}]},
            )
        ],
    )
    document = DocumentEvidence(
        chunk_id="chunk-1",
        text="KA45 qiyməti 110 AZN-dir.",
        file_id=11,
        filename="Zəmanət şərtləri.pdf",
        locator={"page": 7},
        retrieval_method="hybrid",
        score=0.9,
    )
    return build_evidence_bundle([structured], [document])


def test_user_citations_show_only_the_document_name() -> None:
    bundle = _document_bundle()
    rendered = "\n".join(f"[{item.number}] {item.label}" for item in bundle.citations)
    assert rendered == "[1] Price list Diller.xlsx\n[2] Zəmanət şərtləri.pdf"


def test_citation_label_falls_back_to_the_azerbaijani_class_name() -> None:
    structured = StructuredEvidence(
        tool_name="resolve_product_price",
        status="OK",
        data={"price": {"amount": "125.0000", "currency": "AZN"}},
        sources=[BusinessSource(source_type="TOOL", locator={"record_id": 4})],
    )
    bundle = build_evidence_bundle([structured], [])
    assert [item.label for item in bundle.citations] == ["Biznes bazası"]


def test_no_technical_metadata_reaches_the_rendered_citation_list() -> None:
    bundle = _document_bundle()
    rendered = "\n".join(item.label for item in bundle.citations)
    for token in ("{", "}", "[", "]", "blocks", "sheet", "Sheet1", "range", "A30:C30", "page"):
        assert token not in rendered


def test_evidence_prompt_keeps_the_exact_locator_for_the_model() -> None:
    bundle = _document_bundle()
    prompt = _bundle_prompt(bundle)
    assert "A30:C30" in prompt and "Sheet1" in prompt
    for citation in bundle.citations:
        assert citation.locator is not None
        assert json.dumps(citation.locator, ensure_ascii=False) in prompt
