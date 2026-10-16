"""Scanned PDF OCR is local, bounded and non-authoritative until indexed."""

import subprocess
from pathlib import Path
from typing import BinaryIO, cast

import pytest
from pypdf import PdfWriter

from office_assistant.infrastructure.database.models import DocumentProcessingStatus
from office_assistant.knowledge.chunking import chunk_normalized_document
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.processing.parsers import ParserRegistry
from office_assistant.processing.pdf_ocr import (
    OcrPageResult,
    PdfOcrConfig,
    PdfOcrService,
)
from office_assistant.processing.worker_service import searchable_parse_status


def _blank_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    with path.open("wb") as handle:
        writer.write(handle)


def test_scanned_pdf_becomes_indexable_with_original_page_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "scanned.pdf"
    _blank_pdf(path)
    engine = PdfOcrService()

    class FakeSession:
        attempted_pages = 1
        recovered_pages = 1

        def read_page(self, page_number: int) -> OcrPageResult:
            assert page_number == 1
            return OcrPageResult(text="KA45 məhsul kataloqu")

    monkeypatch.setattr(engine, "session", lambda _: FakeSession())
    parser = ParserRegistry(pdf_ocr=engine).for_filename(path.name)
    parsed = parser.parse(path, source_file_id=9, media_type="application/pdf")
    assert searchable_parse_status(parsed) == DocumentProcessingStatus.SUCCEEDED
    assert not parsed.requires_ocr
    assert [block.block_type for block in parsed.blocks] == ["page_ocr"]
    assert parsed.blocks[0].locator == {"page": 1, "extraction": "tesseract"}
    assert parsed.metadata["ocr"] == {
        "engine": "tesseract",
        "attempted_pages": 1,
        "recovered_pages": 1,
        "unresolved_pages": 0,
    }
    chunks = chunk_normalized_document(
        parsed.as_dict(),
        source_file_id=9,
        parse_run_id=3,
        config=ChunkingConfig("test", 1800, 180),
    )
    assert len(chunks) == 1
    assert "KA45" in chunks[0].text
    assert chunks[0].locator["blocks"][0]["page"] == 1


def test_blank_page_remains_deferred_if_ocr_returns_no_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "blank.pdf"
    _blank_pdf(path)
    engine = PdfOcrService()

    class EmptySession:
        attempted_pages = 1
        recovered_pages = 0

        def read_page(self, _: int) -> OcrPageResult:
            return OcrPageResult(reason="OCR_NO_TEXT")

    monkeypatch.setattr(engine, "session", lambda _: EmptySession())
    parsed = ParserRegistry(pdf_ocr=engine).for_filename(path.name).parse(
        path, source_file_id=1, media_type="application/pdf"
    )
    assert searchable_parse_status(parsed) == DocumentProcessingStatus.DEFERRED
    assert parsed.requires_ocr
    assert "OCR_NO_TEXT" in parsed.warnings[0]


def test_subprocess_is_bounded_and_never_uses_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"%PDF")
    seen: list[tuple[list[str], dict[str, object]]] = []
    raster: list[Path] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen.append((args, kwargs))
        if args[0] == "pdftoppm":
            raster_file = Path(args[-1] + ".png")
            raster_file.write_bytes(b"PNG")
            raster.append(raster_file)
        else:
            out = cast(BinaryIO, kwargs["stdout"])
            out.write("Məhsul KA45".encode())
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = PdfOcrService(PdfOcrConfig(languages="aze+eng", max_pages=2)).session(
        pdf
    ).read_page(2)
    assert result.text == "Məhsul KA45"
    assert len(seen) == 2
    assert seen[0][0][0] == "pdftoppm"
    assert seen[1][0][0] == "tesseract"
    assert seen[1][0][seen[1][0].index("-l") + 1] == "aze+eng"
    assert all("shell" not in kw for _, kw in seen)
    assert all(float(kw["timeout"]) <= 15 for _, kw in seen)
    assert all(kw["stderr"] == subprocess.DEVNULL for _, kw in seen)
    assert not raster[0].exists()


def test_missing_ocr_binary_is_reported_without_exposing_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("secret/private/source-file.pdf")

    monkeypatch.setattr(subprocess, "run", unavailable)
    pdf = tmp_path / "private.pdf"
    pdf.write_bytes(b"stub")
    result = PdfOcrService().session(pdf).read_page(1)
    assert result == OcrPageResult(reason="OCR_ENGINE_UNAVAILABLE")
    assert "private" not in str(result)


def test_page_limit_never_launches_a_second_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def always_fail(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess([], 1)

    monkeypatch.setattr(subprocess, "run", always_fail)
    session = PdfOcrService(PdfOcrConfig(max_pages=1)).session(tmp_path / "a.pdf")
    assert session.read_page(1).reason == "OCR_RENDER_FAILED"
    assert session.read_page(2).reason == "OCR_PAGE_LIMIT"
    assert calls == 1


def test_invalid_ocr_languages_are_rejected() -> None:
    for languages in ["aze; rm -rf", "eng rus", "../eng", "ENG"]:
        with pytest.raises(ValueError, match="language"):
            PdfOcrConfig(languages=languages)


def test_page_timeout_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timed_out(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(["pdftoppm", "secret.pdf"], 15, output=b"private")

    monkeypatch.setattr(subprocess, "run", timed_out)
    result = PdfOcrService().session(tmp_path / "source.pdf").read_page(1)
    assert result.reason == "OCR_PAGE_TIMEOUT"
    assert result.text == ""
