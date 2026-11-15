"""Sub-question decomposition (promt.md 5, "Decomposition").

A single message can ask unrelated things at once, e.g. scenario G: "Bu printerə kabel
varmı, son müqavilədə təchizatçı kimdir və anbarda neçədir?" - three source groups in one
sentence. Splitting it here lets the planner route each part independently instead of
collapsing the whole message into one category (defect F4).

The splitter is deliberately shallow: it separates on conjunctions and punctuation and
attaches source hints from recognized concepts. Deep decomposition is Phase 5's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from .concepts import ConceptAnalysis, analyze_concepts
from .lexicon import SUB_QUESTION_SOURCE_HINTS

_DELIMITER: Final[re.Pattern[str]] = re.compile(
    r"(?:[,;]|\b(?:və|ve|həm|hem|and)\b)", re.IGNORECASE
)

MINIMUM_SOURCE_ORDER: Final[tuple[str, ...]] = (
    "postgres",
    "document",
    "graph",
    "image",
    "memory",
    "web",
)


@dataclass(frozen=True, slots=True)
class DecomposedSubQuestion:
    """One independent part of a multi-topic message."""

    text: str
    concepts: tuple[str, ...]
    required_sources: tuple[str, ...]


def _source_hints(concepts: tuple[str, ...]) -> tuple[str, ...]:
    hinted: set[str] = set()
    for concept in concepts:
        hinted.update(SUB_QUESTION_SOURCE_HINTS.get(concept, ()))
    return tuple(item for item in MINIMUM_SOURCE_ORDER if item in hinted)


def _is_meaningful(fragment: str) -> bool:
    return any(character.isalpha() for character in fragment) and len(fragment.split()) >= 2


def decompose(
    question: str, analysis: ConceptAnalysis | None = None
) -> tuple[DecomposedSubQuestion, ...]:
    """Split a message into independent sub-questions, tagging each with source hints."""

    root = analysis if analysis is not None else analyze_concepts(question)
    fragments = [item.strip(" \t?.,;:!") for item in _DELIMITER.split(question)]
    meaningful = [item for item in fragments if _is_meaningful(item)]
    if not meaningful:
        return ()
    if len(meaningful) == 1:
        return (
            DecomposedSubQuestion(
                text=meaningful[0],
                concepts=root.canonicals,
                required_sources=_source_hints(root.canonicals),
            ),
        )
    decomposed: list[DecomposedSubQuestion] = []
    for fragment in meaningful:
        fragment_analysis = analyze_concepts(fragment)
        decomposed.append(
            DecomposedSubQuestion(
                text=fragment,
                concepts=fragment_analysis.canonicals,
                required_sources=_source_hints(fragment_analysis.canonicals),
            )
        )
    return tuple(decomposed)
