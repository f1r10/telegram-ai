"""Bounded, code-safe typo correction for folded query tokens.

Rules (promt.md 5, "Typo / alias / transliteration tolerance"):

* Product codes, model numbers and serial numbers are **never** corrected. Tokens that
  carry a digit, and tokens that make up an authoritative identifier, are excluded before
  any distance is computed.
* Short tokens are left alone: they carry little signal and correcting them is where
  false positives come from.
* An ambiguous correction is *not* applied. The alternatives are returned so the caller
  can ask the user instead of silently picking one (promt.md 5, "If a correction is
  ambiguous, show alternatives and ask - do not silently pick").

The distance is a bounded Damerau-Levenshtein: it is abandoned as soon as it exceeds the
caller's limit, so the cost stays linear in the vocabulary for a fixed bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .lexicon import CORRECTABLE_VOCABULARY

#: Tokens shorter than this are never corrected.
MIN_CORRECTABLE_LENGTH: Final[int] = 4
#: Edit-distance budget for tokens of length <= 4 (see ``_max_distance``).
SHORT_TOKEN_DISTANCE: Final[int] = 1
#: Edit-distance budget for longer tokens.
DEFAULT_DISTANCE: Final[int] = 2


@dataclass(frozen=True, slots=True)
class TokenCorrection:
    """One applied, unambiguous correction."""

    original: str
    corrected: str
    distance: int


@dataclass(frozen=True, slots=True)
class TokenAmbiguity:
    """A token with several equally plausible corrections; left uncorrected on purpose.

    ``candidates`` is what the caller shows the user when it asks instead of picking
    (promt.md 5, "If a correction is ambiguous, show alternatives and ask").
    """

    token: str
    candidates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TypoResult:
    """The corrected token stream and everything the corrector decided."""

    corrected_tokens: tuple[str, ...]
    corrections: tuple[TokenCorrection, ...]
    #: Tokens with more than one equally plausible correction, with their alternatives.
    ambiguous: tuple[TokenAmbiguity, ...]


def bounded_distance(left: str, right: str, limit: int) -> int:
    """Damerau-Levenshtein distance, returning ``limit + 1`` once the bound is exceeded."""

    if left == right:
        return 0
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous_previous: list[int] = []
    previous: list[int] = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current: list[int] = [row]
        for column, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            value = min(
                previous[column] + 1,
                current[column - 1] + 1,
                previous[column - 1] + cost,
            )
            if (
                row > 1
                and column > 1
                and left_char == right[column - 2]
                and left[row - 2] == right_char
            ):
                value = min(value, previous_previous[column - 2] + 1)
            current.append(value)
        if min(current) > limit:
            return limit + 1
        previous_previous, previous = previous, current
    return previous[len(right)]


def _max_distance(token: str) -> int:
    return SHORT_TOKEN_DISTANCE if len(token) <= MIN_CORRECTABLE_LENGTH else DEFAULT_DISTANCE


def _is_code_like(token: str) -> bool:
    return any(character.isdigit() for character in token)


def correct_tokens(
    tokens: tuple[str, ...],
    *,
    protected: frozenset[str] = frozenset(),
    vocabulary: tuple[str, ...] = CORRECTABLE_VOCABULARY,
) -> TypoResult:
    """Correct recognizable typos, never identifiers and never ambiguously."""

    known = set(vocabulary)
    corrected: list[str] = []
    corrections: list[TokenCorrection] = []
    ambiguous: list[TokenAmbiguity] = []
    for token in tokens:
        if token in protected or _is_code_like(token) or len(token) < MIN_CORRECTABLE_LENGTH:
            corrected.append(token)
            continue
        if token in known:
            corrected.append(token)
            continue
        limit = _max_distance(token)
        matches = [
            (bounded_distance(token, word, limit), word)
            for word in vocabulary
            if abs(len(word) - len(token)) <= limit
        ]
        hits = [(distance, word) for distance, word in matches if distance <= limit]
        if not hits:
            corrected.append(token)
            continue
        best = min(distance for distance, _ in hits)
        winners = sorted({word for distance, word in hits if distance == best})
        if len(winners) != 1:
            ambiguous.append(TokenAmbiguity(token=token, candidates=tuple(winners)))
            corrected.append(token)
            continue
        winner = winners[0]
        corrections.append(TokenCorrection(token, winner, best))
        corrected.append(winner)
    return TypoResult(
        corrected_tokens=tuple(corrected),
        corrections=tuple(corrections),
        ambiguous=tuple(ambiguous),
    )
