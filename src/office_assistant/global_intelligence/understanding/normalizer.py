"""Deterministic normalization for the understanding layer.

The shipped ``internal_evidence.query_normalization`` module stays the single source of
truth for folding and identifier extraction; this module only adds the derived views the
understanding layer needs (tokens plus the set of tokens that must never be touched).
"""

from __future__ import annotations

from dataclasses import dataclass

from office_assistant.internal_evidence.query_normalization import (
    normalize_user_query,
    strong_identifiers,
)


@dataclass(frozen=True, slots=True)
class NormalizedQuery:
    """A folded query and the authoritative identifiers extracted from the raw text."""

    original: str
    folded: str
    tokens: tuple[str, ...]
    identifiers: tuple[str, ...]
    protected_tokens: frozenset[str]


def _protected_tokens(tokens: tuple[str, ...], identifiers: tuple[str, ...]) -> frozenset[str]:
    """Tokens that belong to an authoritative identifier (or carry a digit).

    ``strong_identifiers`` is the authority (promt.md 5: codes are never fuzzy-mangled),
    so the rule is defined in terms of its output rather than re-derived from a regex.
    """

    known = set(identifiers)
    protected: set[str] = set()
    for token in tokens:
        if any(character.isdigit() for character in token):
            protected.add(token)
            continue
        if token.replace("-", "").upper() in known:
            protected.add(token)
    return frozenset(protected)


def normalize(question: str) -> NormalizedQuery:
    folded = normalize_user_query(question)
    identifiers = strong_identifiers(question)
    tokens = tuple(folded.split())
    return NormalizedQuery(
        original=question,
        folded=folded,
        tokens=tokens,
        identifiers=identifiers,
        protected_tokens=_protected_tokens(tokens, identifiers),
    )
