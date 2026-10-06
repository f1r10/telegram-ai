"""Deterministic Phase 10 routing benchmark contracts and scoring."""

from dataclasses import dataclass

from office_assistant.business_tools.models import BusinessSource
from office_assistant.internal_evidence.delivery import parse_file_delivery_request
from office_assistant.internal_evidence.models import (
    DocumentEvidence,
    RoutingCategory,
    StructuredEvidence,
)
from office_assistant.internal_evidence.router import deterministic_plan


@dataclass(frozen=True, slots=True)
class RoutingBenchmarkCase:
    name: str
    question: str
    expected_category: RoutingCategory
    requires_structured: bool
    requires_documents: bool
    requires_conflict: bool = False
    requires_file_resolution: bool = False


@dataclass(frozen=True, slots=True)
class RoutingObservation:
    name: str
    category: RoutingCategory
    used_structured: bool
    used_documents: bool
    citations_valid: bool = True
    conflict_surfaced: bool = False
    hallucinated_without_source: bool = False
    file_reference_resolved: bool = False


@dataclass(frozen=True, slots=True)
class RoutingBenchmarkMetrics:
    cases: int
    correct_channel_rate: float
    missing_required_channel_rate: float
    unnecessary_internal_call_rate: float
    citation_validity_rate: float
    conflict_surfacing_rate: float
    no_source_hallucination_rate: float
    file_reference_resolution_rate: float


PHASE10_ROUTING_CASES = (
    RoutingBenchmarkCase(
        "structured-only",
        "ABC MMC bu ay nə qədər ödəniş edib?",
        RoutingCategory.INTERNAL_STRUCTURED,
        True,
        False,
    ),
    RoutingBenchmarkCase(
        "document-only",
        "KA45 zəmanət şərti nədir?",
        RoutingCategory.INTERNAL_DOCUMENT,
        False,
        True,
    ),
    RoutingBenchmarkCase(
        "hybrid",
        "KA45-in qiyməti nədir?",
        RoutingCategory.INTERNAL_HYBRID,
        True,
        True,
    ),
    RoutingBenchmarkCase(
        "proposal-hybrid",
        "ABC MMC təklif şərtlərini müqayisə et",
        RoutingCategory.INTERNAL_HYBRID,
        True,
        True,
    ),
    RoutingBenchmarkCase(
        "structured-document-conflict",
        "KA45 qiyməti neçə AZN-dir?",
        RoutingCategory.INTERNAL_HYBRID,
        True,
        True,
        requires_conflict=True,
    ),
    RoutingBenchmarkCase(
        "absent-internal-fact",
        "ZZ99 qiyməti nədir?",
        RoutingCategory.INTERNAL_HYBRID,
        True,
        True,
    ),
    RoutingBenchmarkCase(
        "ambiguous-customer",
        "ABC MMC ödənişləri nə qədərdir?",
        RoutingCategory.INTERNAL_STRUCTURED,
        True,
        False,
    ),
    RoutingBenchmarkCase(
        "general-world",
        "Azərbaycanın paytaxtı haradır?",
        RoutingCategory.GENERAL_CHAT,
        False,
        False,
    ),
    RoutingBenchmarkCase(
        "file-source-follow-up",
        "2-ci mənbəni göndər",
        RoutingCategory.FILE_DELIVERY,
        False,
        False,
        requires_file_resolution=True,
    ),
)


def run_synthetic_routing_benchmark() -> RoutingBenchmarkMetrics:
    """Run deterministic routing plus synthetic conflict/citation invariants."""

    from office_assistant.internal_evidence.service import build_evidence_bundle

    conflict_bundle = build_evidence_bundle(
        [
            StructuredEvidence(
                tool_name="resolve_product_price",
                status="OK",
                data={"price": {"amount": "125", "currency": "AZN"}},
                sources=[BusinessSource(source_type="FILE", file_id=1)],
            )
        ],
        [
            DocumentEvidence(
                chunk_id="benchmark",
                text="KA45 qiyməti 110 AZN-dir.",
                file_id=2,
                filename="benchmark.pdf",
                locator={"page": 1},
                retrieval_method="hybrid",
                score=1.0,
            )
        ],
    )
    conflict_surfaced = any(
        item.conflict_type == "STRUCTURED_VS_DOCUMENT" for item in conflict_bundle.conflicts
    )
    citations_valid = (
        len(conflict_bundle.citations) == 2
        and len({item.evidence_id for item in conflict_bundle.citations}) == 2
    )
    observations: list[RoutingObservation] = []
    for case in PHASE10_ROUTING_CASES:
        if case.requires_file_resolution:
            source_number = parse_file_delivery_request(case.question)
            observations.append(
                RoutingObservation(
                    case.name,
                    RoutingCategory.FILE_DELIVERY,
                    False,
                    False,
                    citations_valid=citations_valid,
                    file_reference_resolved=source_number == 2,
                )
            )
            continue
        plan = deterministic_plan(case.question)
        if plan is None:
            raise ValueError(f"Benchmark case requires model planning: {case.name}")
        observations.append(
            RoutingObservation(
                case.name,
                plan.category,
                plan.use_structured_tools,
                plan.use_document_search,
                citations_valid=citations_valid,
                conflict_surfaced=conflict_surfaced if case.requires_conflict else False,
            )
        )
    return score_routing_benchmark(observations)


def score_routing_benchmark(
    observations: list[RoutingObservation],
) -> RoutingBenchmarkMetrics:
    expected = {item.name: item for item in PHASE10_ROUTING_CASES}
    if set(expected) != {item.name for item in observations}:
        raise ValueError("Observations must contain every Phase 10 benchmark case exactly once")
    total = len(observations)
    correct = 0
    missing = 0
    unnecessary = 0
    valid_citations = 0
    no_source_hallinations = 0
    conflict_total = 0
    conflict_ok = 0
    file_total = 0
    file_ok = 0
    for observed in observations:
        case = expected[observed.name]
        if (
            observed.category == case.expected_category
            and observed.used_structured == case.requires_structured
            and observed.used_documents == case.requires_documents
        ):
            correct += 1
        if (case.requires_structured and not observed.used_structured) or (
            case.requires_documents and not observed.used_documents
        ):
            missing += 1
        if (observed.used_structured and not case.requires_structured) or (
            observed.used_documents and not case.requires_documents
        ):
            unnecessary += 1
        valid_citations += int(observed.citations_valid)
        no_source_hallinations += int(observed.hallucinated_without_source)
        if case.requires_conflict:
            conflict_total += 1
            conflict_ok += int(observed.conflict_surfaced)
        if case.requires_file_resolution:
            file_total += 1
            file_ok += int(observed.file_reference_resolved)
    return RoutingBenchmarkMetrics(
        cases=total,
        correct_channel_rate=correct / total,
        missing_required_channel_rate=missing / total,
        unnecessary_internal_call_rate=unnecessary / total,
        citation_validity_rate=valid_citations / total,
        conflict_surfacing_rate=conflict_ok / conflict_total if conflict_total else 1.0,
        no_source_hallucination_rate=no_source_hallinations / total,
        file_reference_resolution_rate=file_ok / file_total if file_total else 1.0,
    )
