"""Baseline anchors and Phase-1 target gates for the evaluation harness.

Phase 1 landed the query-understanding + planner layer, so the former ``xfail`` gates are now hard
assertions: code-safe identifiers preserve every offline scenario's codes (including D's
"M404dən" -> "M404"), and the understanding targets A/I/K pass. The shipped
``internal_evidence.router.deterministic_plan`` defect reproducers live in
``test_evaluation_harness.py`` and stay green because the router is byte-identical. The full
scorecard is produced by ``scripts/eval_scorecard.py`` and recorded in
``docs/evaluation/BENCHMARK_RESULTS.md``.
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


# Phase 1 made identifier extraction code-safe: an Azerbaijani case suffix absorbed into the ASCII
# tail of a code ("M404dən" -> "M404d") is now stripped, while a genuine model tail ("M404dn") is
# preserved. Every offline scenario's expected identifiers survive, so these are hard assertions.
_PRESERVATION_CASES = [item.id for item in offline_scenarios() if item.expected_identifiers]


@pytest.mark.parametrize("scenario_id", _PRESERVATION_CASES)
def test_identifiers_are_preserved_by_current_normalization(scenario_id: str) -> None:
    """Codes/model numbers survive normalization untouched (a current strength)."""

    result = probe(BY_ID[scenario_id])
    assert result.identifiers_preserved, result.identifiers


@pytest.mark.parametrize("scenario_id", ["A", "I", "K"])
def test_phase1_query_understanding_target(scenario_id: str) -> None:
    result = probe(BY_ID[scenario_id])
    assert result.understanding_target_met, (
        f"concepts_missing={result.concepts_missing} "
        f"route={result.deterministic_intent} general_chat={result.routed_to_general_chat}"
    )
