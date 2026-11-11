"""Bounded, code-safe typo correction (architecture §3, defect F2).

Only known-correct domain words (:data:`TYPO_VOCABULARY`) are ever correction targets, the edit
distance is bounded (1 for short tokens, 2 for long ones), and identifiers/codes are excluded by
the caller. An ambiguous token (more than one candidate) is never silently picked: it is returned
for clarification (promt.md §5).
"""

from __future__ import annotations

from office_assistant.global_intelligence.models import TypoCorrection
from office_assistant.global_intelligence.understanding.lexicon import TYPO_VOCABULARY

# Sorted once so candidate selection and correction order are deterministic across hash seeds.
_VOCABULARY: tuple[str, ...] = tuple(sorted(TYPO_VOCABULARY))


def _edit_distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance with an early exit; returns ``limit + 1`` once it exceeds ``limit``."""

    if a == b:
        return 0
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        row_min = current[0]
        for j, char_b in enumerate(b, start=1):
            cost = 0 if char_a == char_b else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            row_min = min(row_min, current[j])
        if row_min > limit:
            return limit + 1
        previous = current
    return previous[len(b)]


def correct_tokens(
    tokens: list[str], protected: frozenset[str]
) -> tuple[list[str], list[TypoCorrection], list[str]]:
    """Return (corrected_tokens, corrections, ambiguous_tokens).

    A token is left untouched when it is a protected identifier, contains a digit, is shorter than
    four characters, or is already a known domain word. Exactly one candidate within the bounded
    distance is corrected; more than one is flagged ambiguous and kept as-is.
    """

    corrected: list[str] = []
    corrections: list[TypoCorrection] = []
    ambiguous: list[str] = []
    for token in tokens:
        if (
            token in protected
            or any(char.isdigit() for char in token)
            or len(token) < 4
            or token in TYPO_VOCABULARY
        ):
            corrected.append(token)
            continue
        limit = 2 if len(token) >= 8 else 1
        candidates = [
            word
            for word in _VOCABULARY
            if abs(len(word) - len(token)) <= limit and _edit_distance(token, word, limit) <= limit
        ]
        if len(candidates) == 1:
            word = candidates[0]
            corrected.append(word)
            corrections.append(
                TypoCorrection(
                    original=token, corrected=word, distance=_edit_distance(token, word, limit)
                )
            )
        elif len(candidates) > 1:
            corrected.append(token)
            ambiguous.append(token)
        else:
            corrected.append(token)
    return corrected, corrections, ambiguous
