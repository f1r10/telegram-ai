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


def test_docling_rejects_symlinked_pdf_and_invalid_header_before_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models = tmp_path / "models"
    models.mkdir()
    pdf = tmp_path / "original.pdf"
    pdf.write_bytes(b"not really pdf content")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    with pytest.raises(DoclingEvaluationError, match="invalid_pdf_header"):
        docling_pdf(pdf, models)
    pdf.write_bytes(b"%PDF-safe-fixture")
    alias = tmp_path / "alias.pdf"
    alias.symlink_to(pdf)
    with pytest.raises(DoclingEvaluationError, match="invalid_pdf"):
        docling_pdf(alias, models)


def test_docling_refuses_excessive_extracted_text_without_provider_egress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from typing import Any

    from office_assistant.scripts import evaluate_docling

    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-fixture")
    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    document = SimpleNamespace(export_to_markdown=lambda: "a" * (8 * 1024 * 1024 + 1))

    def convert(_path: str) -> SimpleNamespace:
        return SimpleNamespace(document=document)

    def make_converter(**_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(convert=convert)

    def make_object(**_kwargs: Any) -> object:
        return object()

    def make_options(**_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace()

    module = SimpleNamespace(DocumentConverter=make_converter, PdfFormatOption=make_object)
    options = SimpleNamespace(PdfPipelineOptions=make_options)
    models_module = SimpleNamespace(InputFormat=SimpleNamespace(PDF="pdf"))

    def fake_import(name: str) -> SimpleNamespace:
        return {
            "docling.datamodel.base_models": models_module,
            "docling.datamodel.pipeline_options": options,
            "docling.document_converter": module,
        }[name]

    monkeypatch.setattr(evaluate_docling, "import_module", fake_import)
    with pytest.raises(DoclingEvaluationError, match="extracted_text_limit"):
        docling_pdf(pdf, models)
