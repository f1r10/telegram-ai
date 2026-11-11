"""Concept recognition, typo tolerance and connector hypotheses (promt.md §5, §9).

Concept recognition is prefix-stem matching over folded tokens, so inflected Azerbaijani
forms are recognized without a stemmer. A connector hypothesis is deliberately *not* a fact:
it is expanded here and labelled probable later (Phase 5 verifies it against typed
connector attributes).
"""

from __future__ import annotations

from office_assistant.global_intelligence.understanding import (
    CompatibilityHypothesis,
    analyze_concepts,
    connector_hypotheses,
    device_and_accessory,
)


def test_typo_is_recovered_as_a_canonical_concept() -> None:
    analysis = analyze_concepts("Plinter üçün kabel var?")
    assert analysis.canonicals == ("printer", "cable")
    assert analysis.corrections == (("plinter", "printer"),)
    assert analysis.has("printer") is True
    assert analysis.has("camera") is False


def test_devices_and_accessories_are_separated() -> None:
    analysis = analyze_concepts("Kamera üçün hansı kabellər var?")
    assert analysis.devices == ("camera",)
    assert analysis.accessories == ("cable",)


def test_inflected_forms_are_recognized_without_a_stemmer() -> None:
    analysis = analyze_concepts("M404 printerinin qiyməti nədir?")
    assert "printer" in analysis.canonicals
    assert "price" in analysis.canonicals


def test_identifier_is_never_fuzzy_matched_into_a_concept() -> None:
    analysis = analyze_concepts("M404dən anbarda neçə ədəd qalıb?")
    assert analysis.canonicals == ("stock",)


def test_ambiguous_token_asks_with_alternatives() -> None:
    analysis = analyze_concepts("kabea")
    assert analysis.canonicals == ()
    assert analysis.ambiguous_tokens == ("kabea",)
    assert analysis.clarification_options == ("kabel", "kabla")


def test_connector_hypotheses_expand_a_device_without_duplicates() -> None:
    assert connector_hypotheses(("camera",)) == (
        CompatibilityHypothesis("camera", "ethernet"),
        CompatibilityHypothesis("camera", "coax"),
        CompatibilityHypothesis("camera", "power-supply"),
        CompatibilityHypothesis("camera", "hdmi"),
    )
    assert connector_hypotheses(("camera", "camera")) == connector_hypotheses(("camera",))
    assert connector_hypotheses(()) == ()


def test_device_and_accessory_fast_path() -> None:
    assert device_and_accessory("Plinter üçün kabel var?") == (("printer",), ("cable",))
