"""Bounded typo correction: codes, short tokens and ambiguity are never guessed.

The corrector is the only place the engine is allowed to invent a word, so these tests
pin the three safety rules promt.md §5 requires: machine identifiers are never touched,
short tokens are left alone, and an ambiguous correction is refused rather than picked.
"""

from __future__ import annotations

from office_assistant.global_intelligence.understanding.typos import (
    MIN_CORRECTABLE_LENGTH,
    TokenAmbiguity,
    TokenCorrection,
    bounded_distance,
    correct_tokens,
)

VOCABULARY = ("printer", "cable")


def test_bounded_distance_measures_small_edits() -> None:
    assert bounded_distance("kabel", "kabel", 5) == 0
    assert bounded_distance("ab", "ba", 1) == 1  # a transposition is one edit


def test_bounded_distance_abandons_once_over_the_limit() -> None:
    limit = 1
    assert bounded_distance("abc", "xyz", limit) == limit + 1
    assert bounded_distance("a", "abcd", limit) == limit + 1


def test_unambiguous_typo_is_corrected() -> None:
    result = correct_tokens(("plinter",), vocabulary=VOCABULARY)
    assert result.corrected_tokens == ("printer",)
    assert result.corrections == (TokenCorrection("plinter", "printer", 1),)
    assert result.ambiguous == ()


def test_ambiguous_correction_is_not_applied_and_returns_alternatives() -> None:
    result = correct_tokens(("kabea",), vocabulary=("kabel", "kabla"))
    assert result.corrected_tokens == ("kabea",)
    assert result.corrections == ()
    assert result.ambiguous == (TokenAmbiguity("kabea", ("kabel", "kabla")),)


def test_machine_identifiers_are_never_corrected() -> None:
    result = correct_tokens(("m404", "pr1nter"), vocabulary=VOCABULARY)
    assert result.corrected_tokens == ("m404", "pr1nter")
    assert result.corrections == ()
    assert result.ambiguous == ()


def test_protected_tokens_are_never_corrected() -> None:
    result = correct_tokens(("plinter",), protected=frozenset({"plinter"}), vocabulary=VOCABULARY)
    assert result.corrected_tokens == ("plinter",)
    assert result.corrections == ()


def test_short_tokens_are_never_corrected() -> None:
    short = "x" * (MIN_CORRECTABLE_LENGTH - 1)
    result = correct_tokens((short,), vocabulary=("xxxxxxxx",))
    assert result.corrected_tokens == (short,)
    assert result.corrections == ()


def test_known_words_are_left_alone() -> None:
    result = correct_tokens(("printer", "cable"), vocabulary=VOCABULARY)
    assert result.corrected_tokens == ("printer", "cable")
    assert result.corrections == ()
