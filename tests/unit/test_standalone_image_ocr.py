"""Standalone image OCR is local, bounded and never a product or stock assertion."""

import subprocess
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from office_assistant.infrastructure.database.models import DocumentProcessingStatus
from office_assistant.knowledge.chunking import chunk_normalized_document
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.processing.image_ocr import ImageOcrResult, ImageOcrService
from office_assistant.processing.parsers import ParserRegistry
from office_assistant.processing.worker_service import searchable_parse_status


def _png(path: Path, *, width: int = 900, height: int = 450) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00\x00"
    )


def test_uploaded_png_produces_citable_searchable_image_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "QA_IMG_682.png"
    _png(original)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        target = cast(BinaryIO, kwargs["stdout"])
        target.write("Məhsul kodu: QA-IMG-682\nQutu: 8 ədəd\nPartiya: L-731".encode())
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert float(cast(float, kwargs["timeout"])) <= 15
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    parser = ParserRegistry(image_ocr=ImageOcrService()).for_filename("label.png")
    result = parser.parse(original, source_file_id=682, media_type="image/png")
    assert searchable_parse_status(result) == DocumentProcessingStatus.SUCCEEDED
    assert result.requires_vision
    assert not result.requires_ocr
    assert result.blocks[0].block_type == "image_ocr"
    assert result.blocks[0].locator == {"image_index": 1, "extraction": "tesseract"}
    assert result.parser_version == "1.1"
    assert "QA-IMG-682" in result.text()
    chunks = chunk_normalized_document(
        result.as_dict(),
        source_file_id=682,
        parse_run_id=14,
        config=ChunkingConfig("test", 1800, 180),
    )
    assert len(chunks) == 1
    assert "L-731" in chunks[0].text
    assert calls[0][0] == "tesseract"
    assert "--psm" in calls[0]


def test_image_without_ocr_stays_deferred_and_has_no_false_success(tmp_path: Path) -> None:
    original = tmp_path / "label.png"
    _png(original)
    result = ParserRegistry().for_filename("label.png").parse(
        original, source_file_id=2, media_type="image/png"
    )
    assert searchable_parse_status(result) == DocumentProcessingStatus.DEFERRED
    assert result.requires_vision
    assert result.requires_ocr
    assert not result.blocks


def test_empty_ocr_stays_deferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = tmp_path / "label.png"
    _png(original)
    engine = ImageOcrService()
    monkeypatch.setattr(
        engine,
        "read",
        lambda path, *, media_type: ImageOcrResult(reason="IMAGE_OCR_NO_TEXT"),
    )
    parsed = ParserRegistry(image_ocr=engine).for_filename("label.png").parse(
        original, source_file_id=2, media_type="image/png"
    )
    assert searchable_parse_status(parsed) == DocumentProcessingStatus.DEFERRED
    assert parsed.warnings == ["IMAGE_OCR_NO_TEXT"]


def test_oversized_or_decompression_bomb_png_is_not_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "malicious.png"
    _png(image, width=65535, height=65535)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("Tesseract must not be launched")

    monkeypatch.setattr(subprocess, "run", forbidden)
    result = ImageOcrService().read(image, media_type="image/png")
    assert result.reason == "IMAGE_OCR_PIXEL_LIMIT"


def test_tesseract_failure_and_timeout_sanitize_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "private-label.png"
    _png(image)

    def timed_out(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(["tesseract", "private-token"], 15)

    monkeypatch.setattr(subprocess, "run", timed_out)
    result = ImageOcrService().read(image, media_type="image/png")
    assert result == ImageOcrResult(reason="IMAGE_OCR_TIMEOUT")
    assert "private" not in str(result)


def test_spoofed_mime_is_rejected_even_if_image_ocr_enabled(tmp_path: Path) -> None:
    image = tmp_path / "fake.jpg"
    _png(image)
    with pytest.raises(ValueError, match="image_signature_mismatch"):
        ParserRegistry(image_ocr=ImageOcrService()).for_filename("fake.jpg").parse(
            image, source_file_id=1, media_type="image/jpeg"
        )


def test_invalid_language_is_rejected_without_shell_execution() -> None:
    with pytest.raises(ValueError, match="language"):
        ImageOcrService(languages="aze; rm -rf /")
