"""Phase 1 — bounded, code-safe typo correction (defect F2)."""

from __future__ import annotations

from office_assistant.global_intelligence.understanding.typos import (
    _edit_distance,  # pyright: ignore[reportPrivateUsage]
    correct_tokens,
)


def test_edit_distance_bounded_and_exact() -> None:
    assert _edit_distance("plinter", "printer", 2) == 1
    assert _edit_distance("printer", "printer", 2) == 0
    assert _edit_distance("kabel", "kablo", 2) == 2
    # Exceeding the limit short-circuits to limit + 1.
    assert _edit_distance("abcdef", "zyxwvu", 2) == 3


def test_plinter_is_corrected_to_printer() -> None:
    corrected, corrections, ambiguous = correct_tokens(["plinter", "ucun", "kabel"], frozenset())
    assert corrected == ["printer", "ucun", "kabel"]
    assert ambiguous == []
    assert len(corrections) == 1
    assert corrections[0].original == "plinter"
    assert corrections[0].corrected == "printer"
    assert corrections[0].distance == 1


def test_identifiers_and_digit_tokens_are_never_corrected() -> None:
    tokens = ["m404", "prn-m404", "404", "m404dn"]
    corrected, corrections, ambiguous = correct_tokens(tokens, frozenset({"m404"}))
    assert corrected == tokens
    assert corrections == []
    assert ambiguous == []


def test_short_and_known_words_are_left_untouched() -> None:
    tokens = ["ucun", "var", "kabel", "printer"]
    corrected, corrections, _ = correct_tokens(tokens, frozenset())
    assert corrected == tokens
    assert corrections == []


def test_ambiguous_token_is_flagged_not_silently_picked() -> None:
    # "kable" is one edit from both "cable" and "kablo": the corrector must not guess.
    corrected, corrections, ambiguous = correct_tokens(["kable"], frozenset())
    assert corrected == ["kable"]
    assert corrections == []
    assert ambiguous == ["kable"]


def test_correction_is_deterministic_across_calls() -> None:
    first = correct_tokens(["plinter", "kamera", "qiymet"], frozenset())
    second = correct_tokens(["plinter", "kamera", "qiymet"], frozenset())
    assert first == second
