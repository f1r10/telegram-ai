"""Measure the *current* query-understanding layer against the scenarios.

This probe exercises only the deterministic, offline-safe surface: the shipped
Global Intelligence Engine understanding layer (which itself delegates folding to
``normalize_user_query``), the authoritative identifier extractor
``strong_identifiers``, and the real routing function ``deterministic_plan``. That
surface is the guaranteed fallback when the LLM router is unavailable (promt.md
§5), so measuring it shows what the engine can do without a provider. It does not
call an LLM, database, Qdrant or vision provider; scenarios that need those stay
NOT VERIFIED until their phase lands.

Concept recognition is deliberately *not* re-implemented here. The probe delegates
to :func:`office_assistant.global_intelligence.understanding.understand` so the
scorecard measures the engine that ships, not a private copy of its rules - a
duplicate stem table would silently drift from the lexicon and could never see the
typo tolerance ("plinter" -> "printer") that Phase 1 added.
"""

from __future__ import annotations

from dataclasses import dataclass

from office_assistant.global_intelligence.understanding import understand
from office_assistant.internal_evidence.query_normalization import strong_identifiers
from office_assistant.internal_evidence.router import deterministic_plan

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


def _recognize_concepts(question: str, expected: tuple[str, ...]) -> tuple[str, ...]:
    """Which of the scenario's expected canonical concepts the engine recognized.

    Delegated to the shipped understanding layer, so typo tolerance and the shared
    lexicon are measured rather than re-stated here.
    """

    recognized = set(understand(question).concepts)
    return tuple(concept for concept in expected if concept in recognized)


def probe(scenario: EvaluationScenario) -> ProbeResult:
    normalized = understand(scenario.question).normalized_query
    identifiers = strong_identifiers(scenario.question)
    preserved = all(item in identifiers for item in scenario.expected_identifiers)
    recognized = _recognize_concepts(scenario.question, scenario.expected_concepts)
    missing = tuple(item for item in scenario.expected_concepts if item not in recognized)
    plan = deterministic_plan(scenario.question)
    intent = plan.intent if plan is not None else None
    category = str(plan.category) if plan is not None else None
    return ProbeResult(
        scenario_id=scenario.id,
        normalized_query=normalized,
        identifiers=identifiers,
        identifiers_preserved=preserved,
        concepts_recognized=recognized,
        concepts_missing=missing,
        deterministic_intent=intent,
        deterministic_category=category,
        has_deterministic_route=plan is not None,
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
