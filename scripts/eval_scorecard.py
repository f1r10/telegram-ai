#!/usr/bin/env python3
"""Print the Global Intelligence Engine evaluation scorecard (promt.md §17).

Run from the repository root: ``python scripts/eval_scorecard.py``.

Offline-safe: measures the current deterministic query-understanding layer for
the probeable scenarios and marks every provider/database/vision-dependent
scenario NOT VERIFIED with the phase that owns it. No LLM, DB, Qdrant or network
is touched, so this is reproducible in CI and on a laptop.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tests"))

from evaluation.probe import format_scorecard, probe  # noqa: E402
from evaluation.scenarios import SCENARIOS, offline_scenarios  # noqa: E402


def main() -> int:
    offline = offline_scenarios()
    print("=" * 78)
    print("BASELINE — deterministic query-understanding probe (offline, no provider)")
    print("=" * 78)
    results = [probe(item) for item in offline]
    print(format_scorecard(results))
    print()
    for item in results:
        print(
            f"[{item.scenario_id}] normalized={item.normalized_query!r} "
            f"identifiers={item.identifiers} recognized={item.concepts_recognized}"
        )
    print()
    print("=" * 78)
    print("FULL SCENARIO MATRIX A-M")
    print("=" * 78)
    for scenario in SCENARIOS:
        if scenario.offline_probe:
            result = probe(scenario)
            status = "TARGET MET" if result.understanding_target_met else "GAP (baseline)"
        else:
            status = f"NOT VERIFIED (phase {scenario.target_phase}; needs live capability)"
        print(f"{scenario.id} | phase {scenario.target_phase} | {status} | {scenario.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
