"""Concept recognition and device -> connector expansion (architecture §3, defects F1/F3).

Recognition matches normalized query tokens against the extended stem lexicon, so Azerbaijani
inflections ("printerinə", "kabellər", "anbarda") are understood without enumerating suffixes.
Expansion turns a device concept into *hypotheses* about the cable/connector families it typically
uses; every hypothesis is PROBABLE until a source corroborates it (promt.md §5). Compatibility is
never asserted from prose similarity.
"""

from __future__ import annotations

from office_assistant.global_intelligence.evidence.confidence import StatementStatus
from office_assistant.global_intelligence.models import ConceptHypothesis
from office_assistant.global_intelligence.understanding.lexicon import (
    CONCEPT_STEMS,
    DEVICE_CONNECTORS,
)


def recognize_concepts(normalized: str) -> tuple[str, ...]:
    """Canonical concepts whose stems appear as a token prefix in the normalized query."""

    tokens = normalized.split()
    found: list[str] = []
    for concept, stems in CONCEPT_STEMS.items():
        if any(token.startswith(stem) for token in tokens for stem in stems):
            found.append(concept)
    return tuple(found)


def expand_concepts(concepts: tuple[str, ...]) -> list[ConceptHypothesis]:
    """Infer connector/cable families for recognized devices, each labelled PROBABLE."""

    hypotheses: list[ConceptHypothesis] = []
    for concept in concepts:
        connectors = DEVICE_CONNECTORS.get(concept)
        if not connectors:
            continue
        hypotheses.append(
            ConceptHypothesis(
                concept="cable",
                related=list(connectors),
                basis=(
                    f"{concept} typically uses {', '.join(connectors)}; "
                    "inferred from domain knowledge, not confirmed by a source"
                ),
                status=StatementStatus.PROBABLE,
            )
        )
    return hypotheses
