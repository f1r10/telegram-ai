"""Deterministic query understanding (promt.md 5).

This package never calls a provider. Given a message it returns the folded text, the
language, the preserved identifiers, the recognized concepts (typo tolerant), the
device-to-connector hypotheses, and the sub-questions. The LLM planner sits *above* this
layer; when it is unavailable this output is the fallback that keeps the bot answering
(promt.md 5, "deterministic fallback if the LLM/provider fails").
"""

from __future__ import annotations

from ..models import QueryUnderstanding
from .concepts import (
    CompatibilityHypothesis,
    ConceptAnalysis,
    ConceptMatch,
    analyze_concepts,
    connector_hypotheses,
    device_and_accessory,
)
from .decomposer import DecomposedSubQuestion, decompose
from .language import detect_language
from .lexicon import INTERNAL_MARKERS
from .normalizer import NormalizedQuery, normalize
from .typos import TokenAmbiguity

__all__ = [
    "INTERNAL_MARKERS",
    "CompatibilityHypothesis",
    "ConceptAnalysis",
    "ConceptMatch",
    "DecomposedSubQuestion",
    "NormalizedQuery",
    "TokenAmbiguity",
    "analyze_concepts",
    "connector_hypotheses",
    "decompose",
    "detect_language",
    "device_and_accessory",
    "normalize",
    "understand",
]


def understand(question: str) -> QueryUnderstanding:
    """Understand one message without any network, database or model call."""

    analysis = analyze_concepts(question)
    hypotheses = connector_hypotheses(analysis.devices)
    return QueryUnderstanding(
        original_query=question,
        normalized_query=analysis.normalized.folded,
        language=detect_language(question),
        tokens=analysis.normalized.tokens,
        identifiers=analysis.normalized.identifiers,
        concepts=analysis.canonicals,
        devices=analysis.devices,
        accessories=analysis.accessories,
        corrections=analysis.corrections,
        ambiguous=analysis.ambiguous_tokens,
        clarification_options=analysis.clarification_options,
        connector_hypotheses=tuple((item.device, item.connector_family) for item in hypotheses),
        clarification_required=bool(analysis.ambiguous),
    )
