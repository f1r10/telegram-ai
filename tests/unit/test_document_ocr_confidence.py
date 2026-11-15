# Third-party document libraries do not all ship complete Pyright stubs; this test doubles the OCR
# seam and the Tesseract process exactly like ``test_ocr_provider_seam.py`` does.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Phase 2 M8: OCR confidence is recorded, never guessed and never acted on.

The engine's per-page score is written beside the text it belongs to, a native-text page says
``extraction: "native"`` and carries **no** score, and the parse run keeps an aggregate. The
retrieval text is untouched: confidence is metadata, not content.
"""

import subprocess
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from evaluation.fixtures import pdf_bytes, png_bytes
from office_assistant.global_intelligence.providers import (
    TesseractOcrProvider,
    mean_word_confidence,
)
from office_assistant.knowledge.chunking import chunk_normalized_document
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.processing.image_ocr import ImageOcrService
from office_assistant.processing.parsers import ParserRegistry
from office_assistant.processing.pdf_ocr import PdfOcrService


class StubOcrProvider:
    """Test double that reports a chosen confidence, exactly as a real engine would."""

    name = "stub"
    version = "test"
    supported_languages = frozenset({"aze", "eng", "rus"})

    def __init__(self, *, text: str = "", confidence: float | None = None) -> None:
        self.text = text
        self.confidence = confidence
        self.calls = 0

    def recognize(
        self,
        image: bytes,
        *,
        languages: tuple[str, ...],
        psm: int,
        timeout_seconds: float,
    ) -> Any:
        self.calls += 1
        return _result(self.text, self.confidence, languages)


def _result(text: str, confidence: float | None, languages: tuple[str, ...]) -> Any:
    from office_assistant.global_intelligence.providers import OcrResult

    return OcrResult(
        text=text,
        confidence=confidence,
        engine="stub",
        version="test",
        languages=languages,
        warnings=(),
    )


def _blank_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    with path.open("wb") as handle:
        writer.write(handle)


def _install_pdftoppm(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        Path(args[-1] + ".png").write_bytes(b"PNG")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_pdf_ocr_page_records_the_engine_confidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "scanned.pdf"
    _blank_pdf(pdf)
    _install_pdftoppm(monkeypatch)
    provider = StubOcrProvider(text="KA45 kataloqu", confidence=0.875)
    parsed = (
        ParserRegistry(pdf_ocr=PdfOcrService(provider=provider), capture_confidence=True)
        .for_filename(pdf.name)
        .parse(pdf, source_file_id=9, media_type="application/pdf")
    )

    assert parsed.blocks[0].metadata == {
        "extraction": "tesseract",
        "confidence": 0.875,
    }
    assert parsed.metadata["confidence"] == {
        "pages": [{"page": 1, "source": "tesseract", "confidence": 0.875}],
        "native_pages": 0,
        "ocr_pages": 1,
        "mean_ocr_confidence": 0.875,
    }
    # The pre-M8 OCR summary is untouched, so existing readers are unaffected.
    assert parsed.metadata["ocr"] == {
        "engine": "tesseract",
        "attempted_pages": 1,
        "recovered_pages": 1,
        "unresolved_pages": 0,
    }


def test_flag_off_keeps_the_pre_m8_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "scanned.pdf"
    _blank_pdf(pdf)
    _install_pdftoppm(monkeypatch)
    provider = StubOcrProvider(text="KA45 kataloqu", confidence=0.875)
    parsed = (
        ParserRegistry(pdf_ocr=PdfOcrService(provider=provider))
        .for_filename(pdf.name)
        .parse(pdf, source_file_id=9, media_type="application/pdf")
    )

    assert parsed.blocks[0].metadata == {}
    assert "confidence" not in parsed.metadata


def test_a_native_text_page_records_extraction_without_a_score(tmp_path: Path) -> None:
    pdf = tmp_path / "native.pdf"
    pdf.write_bytes(pdf_bytes("Warranty period is 24 months."))
    parsed = (
        ParserRegistry(capture_confidence=True)
        .for_filename(pdf.name)
        .parse(pdf, source_file_id=3, media_type="application/pdf")
    )

    assert parsed.blocks[0].block_type == "page_text"
    # No fabricated score: the key is absent, so it is not 1.0 and not 0.0 either.
    assert parsed.blocks[0].metadata == {"extraction": "native"}
    summary = parsed.metadata["confidence"]
    assert summary["mean_ocr_confidence"] is None
    assert summary["pages"] == [{"page": 1, "source": "native", "confidence": None}]
    assert (summary["native_pages"], summary["ocr_pages"]) == (1, 0)


def test_an_image_records_engine_confidence(tmp_path: Path) -> None:
    image = tmp_path / "label.png"
    image.write_bytes(png_bytes("KA45"))
    provider = StubOcrProvider(text="Məhsul kodu: KA45", confidence=0.71)
    parsed = (
        ParserRegistry(image_ocr=ImageOcrService(provider=provider), capture_confidence=True)
        .for_filename("label.png")
        .parse(image, source_file_id=682, media_type="image/png")
    )

    assert parsed.blocks[0].metadata == {"extraction": "tesseract", "confidence": 0.71}
    assert parsed.metadata["confidence"]["mean_ocr_confidence"] == 0.71
    assert parsed.metadata["filename"] == "label.png"
    assert parsed.metadata["ocr"] == {"engine": "tesseract"}


def test_an_image_without_capture_keeps_the_pre_m8_metadata(tmp_path: Path) -> None:
    image = tmp_path / "label.png"
    image.write_bytes(png_bytes("KA45"))
    provider = StubOcrProvider(text="Məhsul kodu: KA45", confidence=0.71)
    parsed = (
        ParserRegistry(image_ocr=ImageOcrService(provider=provider))
        .for_filename("label.png")
        .parse(image, source_file_id=682, media_type="image/png")
    )

    assert parsed.blocks[0].metadata == {}
    assert parsed.metadata == {"filename": "label.png", "ocr": {"engine": "tesseract"}}


def test_mean_word_confidence_skips_non_word_rows_and_sentinels() -> None:
    tsv = "\n".join(
        (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            "1\t1\t0\t0\t0\t0\t0\t0\t0\t0\t-1\t",
            "5\t1\t1\t1\t1\t1\t10\t10\t20\t10\t90.5\tKA45",
            "5\t1\t1\t1\t1\t2\t40\t10\t20\t10\t70\tM404",
            "5\t1\t1\t1\t1\t3\t60\t10\t20\t10\t-1\t",
        )
    )

    # (90.5 + 70) / 2 = 80.25 -> 0.8025, with the -1 sentinel excluded.
    assert mean_word_confidence(tsv) == 0.8025


def test_mean_word_confidence_is_unknown_without_words() -> None:
    header_only = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
        "\tleft\ttop\twidth\theight\tconf\ttext"
    )

    result = mean_word_confidence(header_only)

    assert result is None
    assert result != 0.0


def test_the_provider_asks_for_text_and_tsv_in_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen.append(args)
        base = Path(args[2])
        base.with_suffix(".txt").write_text("KA45\n", encoding="utf-8")
        base.with_suffix(".tsv").write_text(
            "5\t1\t1\t1\t1\t1\t10\t10\t20\t10\t90\tKA45", encoding="utf-8"
        )
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = TesseractOcrProvider(capture_confidence=True).recognize(
        b"image", languages=("aze", "eng"), psm=3, timeout_seconds=5
    )

    assert len(seen) == 1
    assert seen[0][-2:] == ["txt", "tsv"]
    assert "stdout" not in seen[0]
    assert result.text == "KA45\n"
    assert result.confidence == 0.9


def test_the_provider_without_capture_keeps_the_stdout_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen.append(args)
        stream = kwargs["stdout"]
        stream.write(b"KA45\n")  # type: ignore[union-attr]
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = TesseractOcrProvider().recognize(
        b"image", languages=("aze",), psm=6, timeout_seconds=5
    )

    assert seen[0][2] == "stdout"
    assert "txt" not in seen[0]
    assert result.confidence is None
    assert result.text == "KA45\n"


def test_confidence_never_changes_the_retrieval_text() -> None:
    base = {
        "sequence": 1,
        "block_type": "page_ocr",
        "text": "KA45 məhsul kataloqu",
        "locator": {"page": 1, "extraction": "tesseract"},
    }
    without = {**base, "metadata": {}}
    with_confidence = {
        **base,
        "metadata": {"extraction": "tesseract", "confidence": 0.875},
    }
    config = ChunkingConfig("v1-medium-1800-180", 1800, 180)

    plain = chunk_normalized_document(
        {"blocks": [without]}, source_file_id=1, parse_run_id=2, config=config
    )
    scored = chunk_normalized_document(
        {"blocks": [with_confidence]}, source_file_id=1, parse_run_id=2, config=config
    )

    assert [chunk.chunk_id for chunk in plain] == [chunk.chunk_id for chunk in scored]
    assert [chunk.text for chunk in plain] == [chunk.text for chunk in scored]
    assert [chunk.source_text for chunk in plain] == [chunk.source_text for chunk in scored]
