"""Measure the *current* query-understanding layer against the scenarios.

This probe deliberately exercises only the deterministic, offline-safe surface
that exists today (``normalize_user_query``, ``strong_identifiers``,
``deterministic_plan``). That surface is the guaranteed fallback when the LLM
router is unavailable (promt.md §5), so measuring it shows what the engine can
do without a provider. It does not call an LLM, database, Qdrant or vision
provider; scenarios that need those stay NOT VERIFIED until their phase lands.
"""

from __future__ import annotations

from dataclasses import dataclass

from office_assistant.internal_evidence.query_normalization import (
    normalize_user_query,
    strong_identifiers,
)
from office_assistant.internal_evidence.router import deterministic_plan

from .scenarios import EvaluationScenario

# Canonical concept -> accepted normalized surface stems. Normalization folds
# Azerbaijani diacritics (ə->e, ı->i, ...) so stems are written folded. A concept
# is "recognized" only when a query token starts with one of its stems; the typo
# "plinter" intentionally does NOT match "printer", which is the gap Phase 1 fills.
_CONCEPT_STEMS: dict[str, tuple[str, ...]] = {
    "printer": ("printer", "cap"),
    "cable": ("kabel", "cable"),
    "camera": ("kamera", "camera"),
    "stock": ("stok", "anbar", "qalib", "elde"),
    "price": ("qiymet", "price"),
    "warranty": ("zemanet", "warranty"),
    "contract": ("muqavile", "contract"),
    "supplier": ("techizatci", "supplier"),
    "order": ("sifaris", "order"),
    "usb-b": ("usb",),
}


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


def _recognize_concepts(normalized: str, expected: tuple[str, ...]) -> tuple[str, ...]:
    tokens = normalized.split()
    found: list[str] = []
    for concept in expected:
        stems = _CONCEPT_STEMS.get(concept, (concept,))
        if any(token.startswith(stem) for token in tokens for stem in stems):
            found.append(concept)
    return tuple(found)


def probe(scenario: EvaluationScenario) -> ProbeResult:
    normalized = normalize_user_query(scenario.question)
    identifiers = strong_identifiers(scenario.question)
    preserved = all(item in identifiers for item in scenario.expected_identifiers)
    recognized = _recognize_concepts(normalized, scenario.expected_concepts)
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
