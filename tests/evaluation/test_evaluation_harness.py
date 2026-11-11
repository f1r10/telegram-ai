"""Harness self-tests plus the Phase 0 baseline measurement (task brief S3.6, S17).

These tests run fully offline: no PostgreSQL, no Qdrant, no LLM key, no Telegram token. They prove
the harness itself is correct, and they measure the **shipped** routing function so the Phase 0
baseline is evidence rather than opinion.
"""

from __future__ import annotations

import asyncio

import pytest

from office_assistant.internal_evidence.models import RoutingCategory
from office_assistant.internal_evidence.router import deterministic_plan

from .corpus import build_reference_corpus
from .metrics import (
    CONFIRMED,
    PROBABLE,
    UNKNOWN,
    HarnessStatement,
    citation_accuracy,
    citation_presence,
    label_coverage,
    mean,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    unsupported_confirmed_rate,
)
from .runner import EvaluationReport, EvaluationRunner, ProductionRoutingProvider
from .scenarios import SCENARIOS, EvaluationSource

# --------------------------------------------------------------------------------------------------
# Corpus (S17.1)
# --------------------------------------------------------------------------------------------------


def test_corpus_meets_section_17_1_minimums() -> None:
    coverage = build_reference_corpus().coverage()
    assert coverage["pdf"] >= 10, coverage
    assert coverage["docx"] >= 5, coverage
    assert coverage["xlsx"] >= 5, coverage
    assert coverage["images"] >= 10, coverage
    assert coverage["scanned_documents"] >= 5, coverage
    assert coverage["contracts_and_invoices"] >= 5, coverage
    assert coverage["duplicate_groups"] >= 1, coverage
    assert coverage["contradiction_groups"] >= 1, coverage
    assert coverage["unrelated_documents"] >= 5, coverage
    assert coverage["injection_documents"] >= 1, coverage


def test_corpus_covers_all_four_languages() -> None:
    languages = {item.language for item in build_reference_corpus().documents}
    assert {"az", "ru", "en"} <= languages


def test_corpus_is_deterministic_and_ids_are_unique() -> None:
    first = build_reference_corpus()
    second = build_reference_corpus()
    assert first.documents == second.documents
    assert first.images == second.images
    ids = [item.document_id for item in first.documents]
    assert len(ids) == len(set(ids))


def test_same_product_appears_in_several_documents() -> None:
    corpus = build_reference_corpus()
    m404_documents = tuple(item for item in corpus.documents if item.matches("PRN-M404"))
    assert len(m404_documents) >= 4, [item.document_id for item in m404_documents]


# --------------------------------------------------------------------------------------------------
# Scenarios (S17.2)
# --------------------------------------------------------------------------------------------------


def test_scenario_ids_are_exactly_a_through_m() -> None:
    assert [item.scenario_id for item in SCENARIOS] == list("ABCDEFGHIJKLM")


def test_every_scenario_declares_sources_behaviours_and_a_query() -> None:
    for scenario in SCENARIOS:
        assert scenario.query.strip(), scenario.scenario_id
        assert scenario.required_sources, scenario.scenario_id
        assert scenario.expected_behaviours, scenario.scenario_id
        assert scenario.language in {"az", "tr", "ru", "en"}, scenario.scenario_id


def test_multi_topic_scenario_declares_three_sub_questions() -> None:
    scenario_g = next(item for item in SCENARIOS if item.scenario_id == "G")
    assert scenario_g.expected_sub_question_count == 3
    assert scenario_g.required_sources >= {
        EvaluationSource.DOCUMENT,
        EvaluationSource.POSTGRES,
    }


def test_routing_only_scenarios_are_excluded_from_routing_metrics() -> None:
    not_observable = {item.scenario_id for item in SCENARIOS if not item.routing_observable}
    assert not_observable == {"L", "M"}


def test_scenario_relevance_ids_exist_in_the_corpus() -> None:
    corpus = build_reference_corpus()
    known = {item.document_id for item in corpus.documents}
    for scenario in SCENARIOS:
        unknown = set(scenario.relevant_document_ids) - known
        assert not unknown, (
            f"scenario {scenario.scenario_id} references unknown documents {unknown}"
        )


# --------------------------------------------------------------------------------------------------
# Metrics (S17.3)
# --------------------------------------------------------------------------------------------------


def test_recall_at_k_counts_distinct_hits() -> None:
    assert recall_at_k({"a", "b", "c"}, ["x", "a", "y", "b"], 4) == 2 / 3
    assert recall_at_k({"a"}, ["a"], 1) == 1.0
    assert recall_at_k({"a", "b"}, ["c", "d"], 2) == 0.0


def test_recall_is_undefined_without_relevant_documents() -> None:
    with pytest.raises(ValueError, match="undefined"):
        recall_at_k(set(), ["a"], 1)


def test_reciprocal_rank_uses_first_relevant_position() -> None:
    assert reciprocal_rank({"b"}, ["a", "b", "c"]) == 0.5
    assert reciprocal_rank({"z"}, ["a", "b"]) == 0.0


def test_ndcg_at_k_rewards_earlier_hits() -> None:
    early = ndcg_at_k({"a"}, ["a", "x", "y"], 3)
    late = ndcg_at_k({"a"}, ["x", "y", "a"], 3)
    assert early > late
    assert early == 1.0
    assert ndcg_at_k({"a"}, ["x", "y", "z"], 3) == 0.0


def test_citation_accuracy_and_presence() -> None:
    assert citation_accuracy(["s1", "s2"], {"s1"}) == 0.5
    assert citation_accuracy([], {"s1"}) == 1.0
    assert citation_presence(["s1"]) == 1.0
    assert citation_presence([]) == 0.0


def test_unsupported_confirmed_rate_requires_evidence_and_locator() -> None:
    statements = (
        HarnessStatement("a", CONFIRMED, "ev-1", {"page": "3"}),
        HarnessStatement("b", CONFIRMED, "ev-2", None),
        HarnessStatement("c", CONFIRMED, None, {"page": "1"}),
        HarnessStatement("d", PROBABLE, None, None),
        HarnessStatement("e", UNKNOWN),
    )
    assert unsupported_confirmed_rate(statements) == 2 / 3
    assert unsupported_confirmed_rate(()) == 0.0


def test_label_coverage_counts_only_allowed_labels() -> None:
    statements = (
        HarnessStatement("a", CONFIRMED, "ev-1", {"page": "1"}),
        HarnessStatement("b", "definitely-true"),
    )
    assert label_coverage(statements) == 0.5
    assert label_coverage(()) == 0.0


def test_mean_of_empty_sequence_is_zero() -> None:
    assert mean([]) == 0.0
    assert mean([1.0, 2.0]) == 1.5


# --------------------------------------------------------------------------------------------------
# Baseline measurement of the shipped routing function
# --------------------------------------------------------------------------------------------------


def _baseline_report() -> EvaluationReport:
    return asyncio.run(EvaluationRunner(ProductionRoutingProvider()).run(SCENARIOS))


def test_production_routing_report_is_well_formed() -> None:
    report = _baseline_report()
    assert len(report.outcomes) == len(SCENARIOS)
    aggregates = report.aggregate()
    assert aggregates["scenarios"] == float(len(SCENARIOS))
    for key, value in aggregates.items():
        if key == "scenarios":
            continue  # a count, not a rate
        assert 0.0 <= value <= 1.0, (key, value)
    assert report.retrieval_scenarios == 0, "the routing provider retrieves nothing by design"
    markdown = report.render_markdown()
    assert "routing_coverage_mean" in markdown
    assert "unsafe_general_fallback_rate" in markdown


def test_baseline_reproduces_defect_f1_general_chat_for_company_questions() -> None:
    """Phase 0 failure reproduction (F1 in docs/audit/FAILURE_ANALYSIS.md).

    The shipped ``deterministic_plan`` routes these company questions to the general-chat path,
    which answers from model memory instead of company sources. Phase 1 fixes this; when it does,
    these assertions must be inverted together with a recorded benchmark delta - they are the
    regression guard for the defect, not a description of intended behaviour.
    """

    for question in ("Plinter üçün kabel var?", "Kamera üçün hansı kabellər var?"):
        plan = deterministic_plan(question)
        assert plan is not None, question
        assert plan.category is RoutingCategory.GENERAL_CHAT, (question, plan.category)
        assert plan.allow_general_knowledge is True, question


def test_baseline_reproduces_defect_f4_multi_topic_question_is_not_decomposed() -> None:
    """Phase 0 failure reproduction (F4): one category cannot hold three source groups."""

    scenario_g = next(item for item in SCENARIOS if item.scenario_id == "G")
    plan = deterministic_plan(scenario_g.query)
    assert plan is not None, scenario_g.query
    assert plan.category is RoutingCategory.GENERAL_CHAT
    assert plan.use_structured_tools is False
    assert plan.use_document_search is False


def test_baseline_reproduces_defect_f2_identifier_is_captured_with_a_trailing_dash() -> None:
    """Phase 0 failure reproduction (F2): 'PRN-M404-un' yields the identifier 'PRN-M404-'."""

    plan = deterministic_plan("PRN-M404-ün qiyməti neçə AZN-dir?")
    assert plan is not None
    assert plan.business_tool_calls, plan
    assert "PRN-M404-" in plan.business_tool_calls[0].arguments_json, plan.business_tool_calls[0]
