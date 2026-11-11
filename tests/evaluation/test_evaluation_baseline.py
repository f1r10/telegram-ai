"""Baseline anchors and Phase-1 target gates for the evaluation harness.

The suite stays green at baseline: identifier preservation is a current strength
and is asserted directly; the Phase-1 understanding targets (A/I/K) are marked
``xfail`` until the Global Intelligence Engine query-understanding layer lands,
then flipped to hard assertions. The full baseline scorecard is produced by
``scripts/eval_scorecard.py`` and recorded in docs/evaluation/BENCHMARK_RESULTS.md.
"""

from __future__ import annotations

import pytest

from .probe import probe
from .scenarios import BY_ID, offline_scenarios


def test_harness_probes_every_offline_scenario() -> None:
    scenarios = offline_scenarios()
    assert scenarios, "expected at least one offline-probeable scenario"
    results = [probe(item) for item in scenarios]
    assert {item.scenario_id for item in results} == {item.id for item in scenarios}


# D carries a known baseline defect: the Azerbaijani ablative suffix "-dən"
# contaminates the extracted identifier (M404dən -> M404D) and the two extractors
# disagree on Unicode word boundaries, so the stock question misroutes to general
# chat. Phase 1 identifier handling fixes it; until then it is an expected failure.
_PRESERVATION_CASES = [
    pytest.param(
        item.id,
        marks=pytest.mark.xfail(
            reason="Baseline defect: AZ ablative suffix contaminates identifier (M404D)",
            strict=False,
        )
        if item.id == "D"
        else [],
    )
    for item in offline_scenarios()
    if item.expected_identifiers
]


@pytest.mark.parametrize("scenario_id", _PRESERVATION_CASES)
def test_identifiers_are_preserved_by_current_normalization(scenario_id: str) -> None:
    """Codes/model numbers survive normalization untouched (a current strength)."""

    result = probe(BY_ID[scenario_id])
    assert result.identifiers_preserved, result.identifiers


@pytest.mark.parametrize("scenario_id", ["A", "I", "K"])
@pytest.mark.xfail(
    reason="Phase 1 not implemented: typo/concept understanding + deterministic routing",
    strict=False,
)
def test_phase1_query_understanding_target(scenario_id: str) -> None:
    result = probe(BY_ID[scenario_id])
    assert result.understanding_target_met, (
        f"concepts_missing={result.concepts_missing} "
        f"route={result.deterministic_intent} general_chat={result.routed_to_general_chat}"
    )
