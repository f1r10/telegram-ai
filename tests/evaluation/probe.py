"""Measure the query-understanding + planning layer against the scenarios.

Phase 1 replaced the measured surface: the probe now exercises the new, fully deterministic and
offline-safe ``global_intelligence`` understanding (``understand_query``) and planner
(``plan_query``). Neither calls an LLM, database, Qdrant or vision provider, so this still measures
the guaranteed fallback capability (promt.md §5) — now with typo tolerance, concept expansion and
code-safe identifiers. The *shipped* ``internal_evidence.router.deterministic_plan`` is measured
separately by ``runner.ProductionRoutingProvider`` and the defect-reproducer tests, and is left
byte-identical; scenarios that need live retrieval stay NOT VERIFIED until their phase lands.
"""

from __future__ import annotations

from dataclasses import dataclass

from office_assistant.global_intelligence.planning.planner import plan_query
from office_assistant.global_intelligence.understanding import understand_query

from .scenarios import EvaluationScenario


@dataclass(frozen=True, slots=True)
class ProbeResult:
    scenario_id: str
    normalized_query: str
    identifiers: tuple[str, ...]
    identifiers_preserved: bool
    concepts_recognized: tuple[str, ...]
    concepts_missing: tuple[str, ...]
    deterministic_intent: str | None
    deterministic_category: str | None
    has_deterministic_route: bool
    routed_to_general_chat: bool

    @property
    def understanding_target_met(self) -> bool:
        """Phase-1 understanding target: identifiers kept, every canonical concept
        recovered from the user's wording, and a correct deterministic internal
        route (not misrouted to general chat, not LLM-only)."""

        return (
            self.identifiers_preserved
            and not self.concepts_missing
            and self.has_deterministic_route
            and not self.routed_to_general_chat
        )


def probe(scenario: EvaluationScenario) -> ProbeResult:
    understanding = understand_query(scenario.question)
    plan = plan_query(scenario.question, understanding)
    preserved = all(item in understanding.identifiers for item in scenario.expected_identifiers)
    recognized = tuple(
        concept for concept in scenario.expected_concepts if concept in understanding.concepts
    )
    missing = tuple(
        concept for concept in scenario.expected_concepts if concept not in understanding.concepts
    )
    category = str(plan.category)
    return ProbeResult(
        scenario_id=scenario.id,
        normalized_query=understanding.normalized_query,
        identifiers=understanding.identifiers,
        identifiers_preserved=preserved,
        concepts_recognized=recognized,
        concepts_missing=missing,
        deterministic_intent=plan.intent,
        deterministic_category=category,
        has_deterministic_route=plan.deterministic,
        routed_to_general_chat=category == "GENERAL_CHAT",
    )


def scorecard(scenarios: tuple[EvaluationScenario, ...]) -> list[ProbeResult]:
    return [probe(item) for item in scenarios]


def format_scorecard(results: list[ProbeResult]) -> str:
    lines = [
        "id | ids kept | concepts missing | det. route | general-chat | target",
        "---|------------------|------------------|---------------------|-----------------------|-------",
    ]
    for item in results:
        route = item.deterministic_intent or "(none -> LLM only)"
        lines.append(
            f"{item.scenario_id} | {item.identifiers_preserved} | "
            f"{','.join(item.concepts_missing) or '-'} | {route} | "
            f"{item.routed_to_general_chat} | {item.understanding_target_met}"
        )
    met = sum(1 for item in results if item.understanding_target_met)
    lines.append(f"\nUnderstanding target met: {met}/{len(results)}")
    return "\n".join(lines)
