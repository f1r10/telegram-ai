"""Phase 1 — concept recognition and device -> connector hypotheses (defects F1/F3)."""

from __future__ import annotations

from office_assistant.global_intelligence.evidence.confidence import StatementStatus
from office_assistant.global_intelligence.understanding.concepts import (
    expand_concepts,
    recognize_concepts,
)


def test_recognizes_inflected_azerbaijani_concepts() -> None:
    assert recognize_concepts("printerine hansi kabel uygundur") == ("printer", "cable")
    assert recognize_concepts("kamera ucun hansi kabeller var") == ("cable", "camera")
    assert recognize_concepts("anbarda nece eded qalib") == ("stock",)


def test_recognizes_multilingual_stems() -> None:
    assert "printer" in recognize_concepts("принтер кабель")
    assert "cable" in recognize_concepts("kablo fiyat")


def test_no_concepts_for_small_talk() -> None:
    assert recognize_concepts("salam necesen") == ()


def test_device_expansion_yields_probable_cable_hypotheses() -> None:
    hypotheses = expand_concepts(("printer",))
    assert len(hypotheses) == 1
    assert hypotheses[0].concept == "cable"
    assert "usb-b" in hypotheses[0].related
    assert hypotheses[0].status is StatementStatus.PROBABLE
    assert "not confirmed" in hypotheses[0].basis


def test_camera_expansion_lists_camera_connectors() -> None:
    hypotheses = expand_concepts(("camera",))
    assert len(hypotheses) == 1
    assert {"bnc", "rj45"} <= set(hypotheses[0].related)
    assert hypotheses[0].status is StatementStatus.PROBABLE


def test_non_device_concepts_do_not_expand() -> None:
    assert expand_concepts(("cable", "price")) == []
