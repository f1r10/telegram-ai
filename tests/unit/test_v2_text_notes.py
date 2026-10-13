"""Natural-language text saves require explicit intent and user confirmation."""

from office_assistant.integrations.telegram.handlers import (
    _is_save_intent,  # pyright: ignore[reportPrivateUsage]
    _note_payload,  # pyright: ignore[reportPrivateUsage]
)


def test_explicit_fact_can_be_staged_as_document() -> None:
    assert _note_payload("Bazaya yaz: KA45 50 AZN-dir") == "KA45 50 AZN-dir"
    assert _note_payload("yadda saxla - KA45 stok 12 ədəd") == "KA45 stok 12 ədəd"
    assert _note_payload("bazaya yaz: 2026-cı il təklifi") == "2026-cı il təklifi"


def test_normal_question_never_creates_a_write_intent() -> None:
    assert _note_payload("KA45 50 AZN-dir") is None
    assert _note_payload("Bazada nə var?") is None
    assert _note_payload("Bu il A şirkətinə nə qədər satmışıq?") is None
    assert _is_save_intent("Bunu bazaya yaz")
    assert not _is_save_intent("Bu il bazaya yazılanlar neçədir?")


def test_empty_and_huge_note_cannot_enter_ingestion() -> None:
    assert _note_payload("Bazaya yaz: ") is None
    assert _note_payload("Bazaya yaz: " + "X" * 4001) is None
