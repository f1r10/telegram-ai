"""Docling evaluation does not call providers or report private truth strings."""

from pathlib import Path

import pytest

from office_assistant.scripts.evaluate_docling import (
    DoclingEvaluationError,
    docling_pdf,
    normalize,
    score_phrases,
)


def test_human_verified_phrase_coverage_is_deterministic() -> None:
    result = score_phrases(
        "Qiymət: 50 AZN. KOD KA45",
        "KOD: KA45 və qiymət 50 AZN.",
        ["KA45", "50 AZN", "digər sətir"],
    )
    assert result.samples == 3
    assert result.native_hits == 2
    assert result.docling_hits == 2
    assert result.result()["native_phrase_recall"] == 0.6667
    assert normalize("  İLK    Məhsul  ") == "i̇lk məhsul"


def test_empty_or_oversized_labels_are_rejected() -> None:
    with pytest.raises(ValueError):
        score_phrases("source", "other", [])
    with pytest.raises(ValueError):
        score_phrases("source", "other", ["   "])
    with pytest.raises(ValueError):
        score_phrases("source", "other", ["a" * 251])


def test_no_model_download_allowed_without_explicit_offline_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-demo")
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    with pytest.raises(DoclingEvaluationError, match="offline_environment_required"):
        docling_pdf(pdf, models)
