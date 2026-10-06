import asyncio
from typing import cast
from uuid import UUID, uuid4

from office_assistant.ai.schemas import GroundedModelOutput
from office_assistant.business.normalization import normalize_customer_name
from office_assistant.business_tools.models import BusinessSource, ToolResult, ToolStatus
from office_assistant.business_tools.registry import BusinessToolRegistry
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.internal_evidence.delivery import parse_file_delivery_request
from office_assistant.internal_evidence.document_search import (
    DocumentRelevancePolicy,
    DocumentSearchInput,
    InternalDocumentSearch,
)
from office_assistant.internal_evidence.models import (
    DocumentEvidence,
    EvidencePlan,
    PlannedToolCall,
    RoutingCategory,
    StructuredEvidence,
)
from office_assistant.internal_evidence.query_normalization import (
    normalize_user_query,
    strong_identifiers,
)
from office_assistant.internal_evidence.router import deterministic_plan, normalize_tool_plan
from office_assistant.internal_evidence.service import (
    InternalEvidenceService,
    _validated_synthesis,  # pyright: ignore[reportPrivateUsage]
    build_evidence_bundle,
)
from office_assistant.knowledge.models import RetrievedChunk
from office_assistant.knowledge.retrieval import KnowledgeRetrievalService


def _chunk(identifier: str, score: float, *, filename: str = "source.txt") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4().hex,
        text=f"{identifier} üçün qiymət və zəmanət məlumatı.",
        source_filename=filename,
        source_file_id=abs(hash((identifier, score))) % 10000 + 1,
        parse_run_id=1,
        locator={"line": 1},
        retrieval_method="hybrid",
        score=score,
        rank=1,
    )


class _RetrievalStub:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.queries: list[str] = []

    async def search_internal_knowledge(self, query: str, **kwargs: object) -> list[RetrievedChunk]:
        self.queries.append(query)
        return self.chunks


def _tool_names(question: str) -> tuple[RoutingCategory, list[str]]:
    plan = deterministic_plan(question)
    assert plan is not None
    return plan.category, [item.tool_name for item in plan.business_tool_calls]


def test_clean_and_messy_azerbaijani_route_to_the_same_evidence_intent() -> None:
    pairs = (
        ("KA45-in qiyməti nədir?", "ka45 qiymeti necedi"),
        ("ABC MMC-nin son təklifi hansıdır?", "abc mmc nin son teklifini tap"),
        ("ABC MMC bu ay nə qədər ödəyib?", "abc bu ay ne qeder pul odeyib"),
    )
    for clean, messy in pairs:
        assert _tool_names(clean) == _tool_names(messy)

    clean = deterministic_plan(pairs[1][0])
    messy = deterministic_plan(pairs[1][1])
    assert clean is not None and messy is not None
    assert normalize_customer_name(
        str(clean.business_tool_calls[0].parsed_arguments()["customer"])
    ) == normalize_customer_name(str(messy.business_tool_calls[0].parsed_arguments()["customer"]))
    compact = deterministic_plan("ABC MMCnin son teklifini tap")
    short = deterministic_plan("abcnin teklifini tap")
    assert compact is not None and short is not None
    assert compact.business_tool_calls[0].parsed_arguments()["customer"] == "ABC MMC"
    assert short.business_tool_calls[0].parsed_arguments()["customer"] == "abc"
    missing_customer = deterministic_plan("son 2 teklif arasinda ferq necedi")
    assert missing_customer is not None
    assert missing_customer.category == RoutingCategory.CLARIFICATION_REQUIRED
    assert missing_customer.requires_clarification
    assert not missing_customer.business_tool_calls


def test_query_normalization_does_not_typo_correct_authoritative_identifiers() -> None:
    assert normalize_user_query("qiyməti / məhsul / təklif / mənbə / göndər") == (
        "qiymeti mehsul teklif menbe gonder"
    )
    assert strong_identifiers("KA45 KA-45 KA 45 KA46 KA4S") == (
        "KA45",
        "KA46",
        "KA4S",
    )
    plan = deterministic_plan("KA4S qiymeti necedi")
    assert plan is not None
    assert plan.business_tool_calls[0].parsed_arguments()["product"] == "KA4S"
    possessive = deterministic_plan("KA45in qiymeti necedi")
    assert possessive is not None
    assert possessive.business_tool_calls[0].parsed_arguments()["product"] == "KA45"


def test_file_delivery_accepts_common_ascii_colloquial_forms_without_model() -> None:
    expected = {
        "2-ci mənbəni göndər": 2,
        "2 ci menbeni gonder": 2,
        "2ci menbeni at": 2,
        "ikinci menbeni gonder": 2,
        "1ci fayli at": 1,
        "pdfi gonder": None,
        "pdf-i gonder": None,
        "onu at": None,
        "bunu gonder": None,
        "faylı ver": None,
        "fayl ver": None,
        "1ci fayli ver": 1,
        "2ci menbeni ver": 2,
        "ikinci mənbəni verin": 2,
        "pdf-i ver": None,
        "onu ver": None,
    }
    assert {text: parse_file_delivery_request(text) for text in expected} == expected


def test_ordinary_information_requests_are_not_mistaken_for_file_delivery() -> None:
    # Master Spec §3.8 and §20: "ver" is tolerated for delivery, but it must not drag a
    # plain business question into a file-delivery turn or a structured parameter prompt.
    for text in (
        "KA45-in qiymətini ver",
        "məlumat ver",
        "ABC MMC-nin son təklifini ver",
        "ABC MMC bu ay nə qədər ödəyib",
        "KA45 zəmanət şərtləri nədir?",
        "bu mehsul bizde var?",
    ):
        assert parse_file_delivery_request(text) is False, text
    assert deterministic_plan("KA45-in qiymətini ver") is not None


def test_identifier_aware_relevance_prunes_weak_tails_and_keeps_code_aliases() -> None:
    retrieval = _RetrievalStub(
        [
            _chunk("KA45", 0.83),
            _chunk("Dahua XVR", 0.50, filename="dahua.pdf"),
            _chunk("KA-45", 0.35),
            _chunk("KA46", 0.90),
        ]
    )
    search = InternalDocumentSearch(
        cast(KnowledgeRetrievalService, retrieval),
        DocumentRelevancePolicy(min_hybrid_score=0.55, min_identifier_score=0.30),
    )
    results = asyncio.run(search.search(DocumentSearchInput(query="KA 45 zemaneti", limit=5)))
    assert [item.text.split()[0] for item in results] == ["KA45", "KA-45"]
    assert len(results) < 5
    assert "zemaneti" in retrieval.queries[0]

    no_results = asyncio.run(search.search(DocumentSearchInput(query="KC99-un qiyməti", limit=5)))
    assert no_results == []


def test_non_identifier_relevance_allows_empty_and_never_pads_to_limit() -> None:
    retrieval = _RetrievalStub([_chunk("zəmanət", 0.60), _chunk("unrelated", 0.50)])
    search = InternalDocumentSearch(cast(KnowledgeRetrievalService, retrieval))
    results = asyncio.run(search.search(DocumentSearchInput(query="zemanet sertleri", limit=5)))
    assert len(results) == 1
    assert "zemanet" in retrieval.queries[0] and "zəmanət" in retrieval.queries[0]


def test_citation_pruning_deduplicates_and_renumbers_used_sources() -> None:
    source = BusinessSource(source_type="FILE", file_id=10, filename="prices.xlsx")
    structured = StructuredEvidence(
        tool_name="resolve_product_price",
        status="OK",
        data={"price": {"price_id": str(uuid4()), "amount": "125", "currency": "AZN"}},
        sources=[source],
    )
    documents = [
        DocumentEvidence(
            chunk_id="irrelevant",
            text="No monetary evidence.",
            file_id=11,
            filename="unused.pdf",
            locator={"page": 1},
            retrieval_method="hybrid",
            score=0.6,
        ),
        DocumentEvidence(
            chunk_id="relevant",
            text="KA45 qiyməti 110 AZN-dir.",
            file_id=12,
            filename="price.txt",
            locator={"line": 1},
            retrieval_method="hybrid",
            score=0.8,
        ),
    ]
    bundle = build_evidence_bundle([structured], documents)
    output = GroundedModelOutput(
        answer="Biznes qiyməti 125 AZN [E1], sənəd qiyməti 110 AZN-dir [E3].",
        citations=["E1", "E3"],
        conflict_detected=True,
    )
    answer, citations = _validated_synthesis(output, bundle.citations, bundle.conflicts)
    assert "E3" not in answer
    assert "[1]" in answer and "[2]" in answer and "[3]" not in answer
    assert [item.filename for item in citations] == ["prices.xlsx", "price.txt"]
    assert [item.number for item in citations] == [1, 2]


def test_structured_conflict_preserves_distinct_records_from_the_same_file() -> None:
    sources = [
        BusinessSource(source_type="FILE", file_id=75, locator={"fixture": "phase10"}),
        BusinessSource(source_type="FILE", file_id=75, locator={"fixture": "phase10"}),
    ]
    structured = StructuredEvidence(
        tool_name="resolve_product_price",
        status="CONFLICT",
        data={
            "conflicting_prices": [
                {"price_id": str(uuid4()), "amount": "200", "currency": "AZN"},
                {"price_id": str(uuid4()), "amount": "210", "currency": "AZN"},
            ]
        },
        sources=sources,
    )
    bundle = build_evidence_bundle([structured], [])
    assert len(bundle.citations) == 2
    output = GroundedModelOutput(
        answer="İki aktiv qiymət var: 200 AZN [E1] və 210 AZN [E2].",
        citations=["E1", "E2"],
        conflict_detected=True,
    )
    answer, citations = _validated_synthesis(output, bundle.citations, bundle.conflicts)
    assert "200 AZN [1]" in answer and "210 AZN [2]" in answer
    assert len(citations) == 2


def test_tool_plan_normalization_removes_redundant_product_lookup() -> None:
    plan = EvidencePlan(
        category=RoutingCategory.INTERNAL_STRUCTURED,
        intent="PRODUCT_PRICE",
        use_structured_tools=True,
        use_document_search=False,
        business_tool_calls=[
            PlannedToolCall(tool_name="find_product", arguments_json='{"code_or_alias":"KA45"}'),
            PlannedToolCall(tool_name="resolve_product_price", arguments_json='{"product":"KA45"}'),
        ],
        reason_code="test",
    )
    normalized = normalize_tool_plan(plan)
    assert [item.tool_name for item in normalized.business_tool_calls] == ["resolve_product_price"]


class _ProposalRegistry:
    names = (
        "get_latest_customer_proposal",
        "get_customer_proposals",
        "get_proposal_details",
        "compare_proposals",
    )

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.old_id = UUID("00000000-0000-0000-0000-000000000001")
        self.new_id = UUID("00000000-0000-0000-0000-000000000002")

    async def execute(
        self, name: str, context: object, payload: dict[str, object]
    ) -> ToolResult[object]:
        self.calls.append((name, payload))
        if name == "get_latest_customer_proposal":
            return ToolResult(status=ToolStatus.OK, data={"proposal_id": str(self.new_id)})
        if name == "get_customer_proposals":
            return ToolResult(
                status=ToolStatus.OK,
                data={
                    "items": [
                        {"proposal_id": str(self.new_id)},
                        {"proposal_id": str(self.old_id)},
                    ],
                    "has_more": False,
                    "next_offset": None,
                },
            )
        if name == "get_proposal_details":
            return ToolResult(
                status=ToolStatus.OK,
                data={"proposal": {"proposal_id": str(self.new_id)}, "items": []},
            )
        return ToolResult(status=ToolStatus.OK, data={})


class _ServiceHarness(InternalEvidenceService):
    async def collect(
        self, actor: User, plan: EvidencePlan, question: str
    ) -> tuple[list[StructuredEvidence], int]:
        return await self._collect_structured(actor, plan, question)


def _service_harness(registry: _ProposalRegistry) -> _ServiceHarness:
    service = _ServiceHarness.__new__(_ServiceHarness)
    service.registry = cast(BusinessToolRegistry, registry)
    service.max_tool_calls = 4
    return service


def test_latest_proposal_details_are_loaded_as_a_deterministic_dependency() -> None:
    registry = _ProposalRegistry()
    service = _service_harness(registry)
    plan = deterministic_plan("ABC MMC-nin son təklifində qiymətlər nədir?")
    assert plan is not None
    actor = User(id=1, telegram_user_id=1, role=UserRole.USER, status=UserStatus.ACTIVE)
    results, count = asyncio.run(service.collect(actor, plan, "son təklifdə nə var"))
    assert [name for name, _ in registry.calls] == [
        "get_latest_customer_proposal",
        "get_proposal_details",
    ]
    assert registry.calls[1][1] == {"proposal_id": str(registry.new_id)}
    assert [item.tool_name for item in results] == [
        "get_latest_customer_proposal",
        "get_proposal_details",
    ]
    assert count == 2


def test_proposal_comparison_calls_tool_in_old_to_new_order() -> None:
    registry = _ProposalRegistry()
    service = _service_harness(registry)
    plan = deterministic_plan("ABC MMC-nin son iki təklifini müqayisə et")
    assert plan is not None
    actor = User(id=1, telegram_user_id=1, role=UserRole.USER, status=UserStatus.ACTIVE)
    _, count = asyncio.run(service.collect(actor, plan, "son iki təklif"))
    assert registry.calls[-1] == (
        "compare_proposals",
        {"proposal_a": str(registry.old_id), "proposal_b": str(registry.new_id)},
    )
    assert count == 2
