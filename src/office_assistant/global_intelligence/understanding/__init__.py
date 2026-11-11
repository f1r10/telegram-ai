"""Stage 1 — query understanding (architecture §3.1, defects F1/F2/F3/F4).

Composes the deterministic, offline-safe understanding surface: normalize, extract authoritative
identifiers (code-safe), bounded typo correction, concept recognition + expansion, a language hint,
and conservative decomposition. ``strong_identifiers`` runs first and its tokens are excluded from
every correction, so product/model/serial codes are never altered (promt.md §5). No provider,
database or vector index is touched here.
"""

from __future__ import annotations

from office_assistant.global_intelligence.models import QueryUnderstanding, SubQuestion
from office_assistant.global_intelligence.understanding.concepts import (
    expand_concepts,
    recognize_concepts,
)
from office_assistant.global_intelligence.understanding.decomposer import decompose
from office_assistant.global_intelligence.understanding.language import detect_language
from office_assistant.global_intelligence.understanding.typos import correct_tokens
from office_assistant.internal_evidence.query_normalization import (
    normalize_user_query,
    strong_identifiers,
)


def understand_query(question: str) -> QueryUnderstanding:
    """Understand a raw user question deterministically, without any provider call."""

    normalized = normalize_user_query(question)
    identifiers = strong_identifiers(question, strip_case_suffix=True)
    protected = frozenset(identifier.casefold() for identifier in identifiers)
    corrected_tokens, corrections, ambiguous = correct_tokens(normalized.split(), protected)
    corrected = " ".join(corrected_tokens)
    concepts = recognize_concepts(corrected)
    hypotheses = expand_concepts(concepts)

    sub_questions: list[SubQuestion] = []
    for part in decompose(question):
        part_normalized = normalize_user_query(part)
        sub_questions.append(
            SubQuestion(
                text=part,
                concepts=list(recognize_concepts(part_normalized)),
                identifiers=list(strong_identifiers(part, strip_case_suffix=True)),
            )
        )

    return QueryUnderstanding(
        original_query=question,
        normalized_query=normalized,
        corrected_query=corrected,
        language=detect_language(question),
        identifiers=identifiers,
        concepts=concepts,
        hypotheses=hypotheses,
        corrections=corrections,
        ambiguous_tokens=ambiguous,
        sub_questions=sub_questions,
        requires_clarification=bool(ambiguous),
    )


__all__ = ["understand_query"]
