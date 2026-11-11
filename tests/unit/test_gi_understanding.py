"""Phase 1 — query understanding orchestration (defects F1/F2/F3/F4)."""

from __future__ import annotations

from office_assistant.global_intelligence.understanding import understand_query
from office_assistant.global_intelligence.understanding.language import detect_language


def test_typo_is_corrected_and_concepts_recovered() -> None:
    result = understand_query("Plinter üçün kabel var?")
    assert result.corrections[0].original == "plinter"
    assert result.corrections[0].corrected == "printer"
    assert "printer" in result.concepts
    assert "cable" in result.concepts
    assert result.identifiers == ()
    assert result.requires_clarification is False
    assert len(result.sub_questions) == 1


def test_ablative_suffix_is_stripped_from_code() -> None:
    result = understand_query("M404dən anbarda neçə ədəd qalıb?")
    assert result.identifiers == ("M404",)
    assert "stock" in result.concepts


def test_genuine_ascii_model_tail_is_preserved() -> None:
    # "M404dn" is a real model suffix, not an Azerbaijani case ending: it must survive.
    result = understand_query("M404dn qiyməti neçədir?")
    assert result.identifiers == ("M404DN",)


def test_code_is_never_typo_corrected() -> None:
    result = understand_query("Plinter M404 üçün kabel lazımdır")
    assert result.identifiers == ("M404",)
    assert {c.original for c in result.corrections} == {"plinter"}
    assert "printer" in result.concepts
    assert "cable" in result.concepts


def test_multi_topic_question_is_decomposed() -> None:
    result = understand_query(
        "Bu printerə kabel varmı, son müqavilədə təchizatçı kimdir və anbarda neçədir?"
    )
    assert len(result.sub_questions) == 3
    assert "contract" in result.sub_questions[1].concepts
    assert "supplier" in result.sub_questions[1].concepts
    assert "stock" in result.sub_questions[2].concepts


def test_single_topic_question_stays_one_sub_question() -> None:
    result = understand_query("Kamera üçün hansı kabellər var?")
    assert len(result.sub_questions) == 1


def test_ambiguous_correction_requests_clarification() -> None:
    result = understand_query("kable üçün")
    assert result.requires_clarification is True
    assert "kable" in result.ambiguous_tokens


def test_device_expansion_is_probable() -> None:
    result = understand_query("Plinter üçün kabel var?")
    assert result.hypotheses
    assert all(h.status.value == "ehtimal" for h in result.hypotheses)


def test_language_hint() -> None:
    assert detect_language("Plinter üçün kabel var?") == "az"
    assert detect_language("Принтер кабель есть?") == "ru"
    assert detect_language("Yazıcı için kablo fiyatı") == "tr"
    assert detect_language("printer cable price") == "en"
