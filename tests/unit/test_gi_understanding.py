"""Deterministic, offline query understanding (promt.md §5).

Everything here runs without a provider, a database or a network. This is the guaranteed
fallback when the LLM router is unavailable, so it is asserted directly rather than through
the router.
"""

from __future__ import annotations

from office_assistant.global_intelligence.understanding import (
    DecomposedSubQuestion,
    decompose,
    detect_language,
    normalize,
    understand,
)


def test_normalize_folds_azerbaijani_diacritics() -> None:
    normalized = normalize("Plinter üçün kabel var?")
    assert normalized.folded == "plinter ucun kabel var"
    assert normalized.tokens == ("plinter", "ucun", "kabel", "var")
    assert normalized.identifiers == ()
    assert normalized.protected_tokens == frozenset()


def test_identifier_tokens_are_protected_from_normalization_edits() -> None:
    normalized = normalize("M404 printerinin qiyməti nədir?")
    assert normalized.identifiers == ("M404",)
    assert normalized.protected_tokens == frozenset({"m404"})


def test_detect_language_covers_az_ru_en_and_the_diacritic_shortcut() -> None:
    assert detect_language("Salam, necəsən?") == "az"
    assert detect_language("Bu printer USB-B portu dəstəkləyir?") == "az"
    assert detect_language("Как дела?") == "ru"
    assert detect_language("Which cable is needed?") == "en"


def test_decompose_splits_a_three_topic_message() -> None:
    parts = decompose(
        "Bu printerə kabel varmı, son müqavilədə təchizatçı kimdir və anbarda neçədir?"
    )
    assert [item.text for item in parts] == [
        "Bu printerə kabel varmı",
        "son müqavilədə təchizatçı kimdir",
        "anbarda neçədir",
    ]
    assert [item.required_sources for item in parts] == [
        ("document", "graph"),
        ("postgres", "document"),
        ("postgres",),
    ]


def test_decompose_keeps_a_single_topic_message_intact() -> None:
    parts = decompose("Plinter üçün kabel var?")
    assert parts == (
        DecomposedSubQuestion(
            text="Plinter üçün kabel var",
            concepts=("printer", "cable"),
            required_sources=("document", "graph"),
        ),
    )


def test_decompose_ignores_a_one_word_message() -> None:
    assert decompose("Salam") == ()


def test_understand_returns_concepts_identifiers_and_hypotheses() -> None:
    understanding = understand("Plinter M404 üçün kabel lazımdır")
    assert understanding.concepts == ("printer", "cable")
    assert understanding.identifiers == ("M404",)
    assert understanding.language == "az"
    assert understanding.clarification_required is False
    assert understanding.connector_hypotheses == (
        ("printer", "usb-b"),
        ("printer", "ethernet"),
        ("printer", "power-supply"),
    )


def test_understand_of_small_talk_is_empty_and_needs_no_clarification() -> None:
    understanding = understand("Salam, necəsən?")
    assert understanding.concepts == ()
    assert understanding.identifiers == ()
    assert understanding.clarification_required is False
