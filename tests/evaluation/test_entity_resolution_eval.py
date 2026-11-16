"""Phase-3 exit evidence: scenario B identity linking and scenario I no-wrong-merge, measured.

The numbers here are generated from ``tests/evaluation/entity_resolution.py`` and are recorded in
``docs/evaluation/BENCHMARK_RESULTS.md``. Nothing private is involved: the registry and the labelled
mentions are code, so the measurements are reproducible on any machine, offline.
"""

import json

from office_assistant.global_intelligence.entities import ResolutionStatus

from .entity_resolution import CaseOutcome, report, run_cases, summarise


def _outcome(case_id: str) -> CaseOutcome:
    return next(item for item in run_cases() if item.case_id == case_id)


def test_every_case_reaches_the_identity_its_label_names() -> None:
    for outcome in run_cases():
        if outcome.expected_entity_id is None:
            assert outcome.status is not ResolutionStatus.RESOLVED, outcome.case_id
        else:
            assert outcome.entity_id == outcome.expected_entity_id, outcome.case_id


def test_resolution_precision_and_recall_are_one_on_the_generated_corpus() -> None:
    numbers = summarise(run_cases())

    assert numbers["wrong_claims"] == 0
    assert numbers["resolution_precision"] == 1.0
    assert numbers["resolution_recall"] == 1.0
    assert numbers["false_merge_rate"] == 0.0


def test_scenario_b_links_one_identity_across_different_surface_forms() -> None:
    for case_id in (
        "b-code",
        "b-canonical",
        "b-alias",
        "b-alias-spaced",
        "b-case-form",
    ):
        outcome = _outcome(case_id)
        assert outcome.entity_id == "product-m404", case_id


def test_scenario_i_keeps_neighbouring_models_and_near_misses_apart() -> None:
    typo = _outcome("i-typo")
    neighbour = _outcome("i-model-neighbour")
    unseen = _outcome("i-unseen-suffix")

    assert typo.status is not ResolutionStatus.RESOLVED
    assert neighbour.entity_id == "product-m405"
    assert "product-m404" not in neighbour.candidate_ids
    assert unseen.status is not ResolutionStatus.RESOLVED


def test_a_shared_name_is_reported_as_ambiguous_without_picking_a_side() -> None:
    outcome = _outcome("i-shared-name")

    assert outcome.status is ResolutionStatus.AMBIGUOUS
    assert set(outcome.candidate_ids) == {"customer-atlas-a", "customer-atlas-b"}


def test_the_measured_report_is_json_serializable_for_the_benchmark_record() -> None:
    encoded = json.dumps(report(), ensure_ascii=False, sort_keys=True)

    assert '"resolution_precision": 1.0' in encoded
    assert '"cases": 13' in encoded
