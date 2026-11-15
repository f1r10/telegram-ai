"""M1: routing PDF and image OCR through the ``OCRProvider`` seam changes nothing.

These tests are the behaviour-preservation contract for the refactor: the seam is exercised
with a stub provider, and the resulting blocks, locators, metadata and reason codes must be
exactly the ones the pre-refactor services produced.
"""

import subprocess
from pathlib import Path

import pytest
from pypdf import PdfWriter

from office_assistant.global_intelligence.providers import (
    RECOGNITION_FAILED,
    TEXT_LIMIT,
    OcrProviderError,
    OcrResult,
    TesseractOcrProvider,
)
from office_assistant.infrastructure.database.models import DocumentProcessingStatus
from office_assistant.processing.image_ocr import (
    MAX_IMAGE_PIXELS,
    MAX_IMAGE_SIDE,
    MAX_INPUT_BYTES,
    ImageOcrService,
)
from office_assistant.processing.parsers import ParserRegistry
from office_assistant.processing.pdf_ocr import (
    MAX_RASTER_BYTES,
    PdfOcrConfig,
    PdfOcrService,
)
from office_assistant.processing.worker_service import searchable_parse_status


class StubOcrProvider:
    """Test double for the seam: returns fixed text, or raises one chosen failure."""

    name = "stub"
    version = "test"
    supported_languages = frozenset({"aze", "eng", "rus"})

    def __init__(self, *, text: str = "", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[tuple[tuple[str, ...], int, float, int]] = []

    def recognize(
        self,
        image: bytes,
        *,
        languages: tuple[str, ...],
        psm: int,
        timeout_seconds: float,
    ) -> OcrResult:
        self.calls.append((languages, psm, timeout_seconds, len(image)))
        if self.error is not None:
            raise self.error
        return OcrResult(
            text=self.text,
            confidence=None,
            engine=self.name,
            version=self.version,
            languages=languages,
            warnings=(),
        )


def _blank_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    with path.open("wb") as handle:
        writer.write(handle)


def _png(path: Path, *, width: int = 900, height: int = 450) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00\x00"
    )


def _install_pdftoppm(monkeypatch: pytest.MonkeyPatch, seen: list[list[str]]) -> None:
    """Let only the rasteriser run; the OCR binary itself is always the stub provider."""

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        seen.append(args)
        Path(args[-1] + ".png").write_bytes(b"PNG")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_stub_provider_reproduces_the_existing_pdf_page_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "scanned.pdf"
    _blank_pdf(pdf)
    seen: list[list[str]] = []
    _install_pdftoppm(monkeypatch, seen)
    provider = StubOcrProvider(text="KA45 məhsul kataloqu")
    parsed = (
        ParserRegistry(pdf_ocr=PdfOcrService(provider=provider))
        .for_filename(pdf.name)
        .parse(pdf, source_file_id=9, media_type="application/pdf")
    )

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
    # Only the rasteriser ran; recognition came from the seam, not a direct binary call.
    assert [args[0] for args in seen] == ["pdftoppm"]
    languages, psm, timeout, image_bytes = provider.calls[0]
    assert languages == ("aze", "eng", "rus")
    assert psm == 3
    assert 0 < timeout <= 15
    assert image_bytes == len(b"PNG")


def test_stub_provider_reproduces_the_existing_image_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = tmp_path / "QA_IMG_682.png"
    _png(image)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("the seam must be the only engine path")

    monkeypatch.setattr(subprocess, "run", forbidden)
    provider = StubOcrProvider(text="Məhsul kodu: QA-IMG-682\nPartiya: L-731")
    result = (
        ParserRegistry(image_ocr=ImageOcrService(provider=provider))
        .for_filename("label.png")
        .parse(image, source_file_id=682, media_type="image/png")
    )

    assert searchable_parse_status(result) == DocumentProcessingStatus.SUCCEEDED
    assert result.requires_vision
    assert not result.requires_ocr
    assert result.blocks[0].block_type == "image_ocr"
    assert result.blocks[0].locator == {"image_index": 1, "extraction": "tesseract"}
    assert result.parser_version == "1.1"
    assert "QA-IMG-682" in result.text()
    languages, psm, _, _ = provider.calls[0]
    assert languages == ("aze", "eng", "rus")
    assert psm == 6


def test_a_raising_provider_defers_the_pdf_run_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "scanned.pdf"
    _blank_pdf(pdf)
    _install_pdftoppm(monkeypatch, [])
    service = PdfOcrService(provider=StubOcrProvider(error=OcrProviderError(RECOGNITION_FAILED)))

    assert service.session(pdf).read_page(1).reason == "OCR_RECOGNITION_FAILED"

    parsed = (
        ParserRegistry(pdf_ocr=service)
        .for_filename(pdf.name)
        .parse(pdf, source_file_id=1, media_type="application/pdf")
    )
    assert searchable_parse_status(parsed) == DocumentProcessingStatus.DEFERRED
    assert parsed.requires_ocr
    assert "OCR_RECOGNITION_FAILED" in parsed.warnings[0]


def test_a_raising_provider_defers_the_image_run_with_a_warning(tmp_path: Path) -> None:
    image = tmp_path / "label.png"
    _png(image)
    parsed = (
        ParserRegistry(
            image_ocr=ImageOcrService(provider=StubOcrProvider(error=OcrProviderError(TEXT_LIMIT)))
        )
        .for_filename("label.png")
        .parse(image, source_file_id=2, media_type="image/png")
    )

    assert searchable_parse_status(parsed) == DocumentProcessingStatus.DEFERRED
    assert parsed.warnings == ["IMAGE_OCR_TEXT_LIMIT"]
    assert not parsed.blocks


@pytest.mark.parametrize(
    ("error", "pdf_reason", "image_reason"),
    [
        (
            OcrProviderError(RECOGNITION_FAILED),
            "OCR_RECOGNITION_FAILED",
            "IMAGE_OCR_RECOGNITION_FAILED",
        ),
        (OcrProviderError(TEXT_LIMIT), "OCR_TEXT_SIZE_LIMIT", "IMAGE_OCR_TEXT_LIMIT"),
        (subprocess.TimeoutExpired(["tesseract"], 1), "OCR_PAGE_TIMEOUT", "IMAGE_OCR_TIMEOUT"),
        (FileNotFoundError("tesseract"), "OCR_ENGINE_UNAVAILABLE", "IMAGE_OCR_ENGINE_UNAVAILABLE"),
        (OSError("disk"), "OCR_LOCAL_IO_FAILURE", "IMAGE_OCR_LOCAL_IO_FAILURE"),
    ],
)
def test_provider_failures_keep_the_existing_reason_vocabulary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    pdf_reason: str,
    image_reason: str,
) -> None:
    pdf = tmp_path / "scanned.pdf"
    _blank_pdf(pdf)
    _install_pdftoppm(monkeypatch, [])
    assert (
        PdfOcrService(provider=StubOcrProvider(error=error)).session(pdf).read_page(1).reason
        == pdf_reason
    )

    image = tmp_path / "label.png"
    _png(image)
    assert (
        ImageOcrService(provider=StubOcrProvider(error=error))
        .read(image, media_type="image/png")
        .reason
        == image_reason
    )


def test_tesseract_is_the_only_registered_production_provider() -> None:
    assert isinstance(PdfOcrService().provider, TesseractOcrProvider)
    assert isinstance(ImageOcrService().provider, TesseractOcrProvider)
    provider = TesseractOcrProvider()
    assert provider.name == "tesseract"
    assert provider.supported_languages == frozenset({"aze", "eng", "rus"})
    assert "tur" not in provider.supported_languages


def test_caps_timeouts_and_language_defaults_are_unchanged() -> None:
    config = PdfOcrConfig()
    assert (
        config.languages,
        config.max_pages,
        config.page_timeout_seconds,
        config.document_timeout_seconds,
        config.max_dimension_pixels,
    ) == ("aze+eng+rus", 80, 15.0, 150.0, 2000)
    assert MAX_RASTER_BYTES == 16 * 1024 * 1024

    service = ImageOcrService()
    assert (service.languages, service.timeout_seconds) == ("aze+eng+rus", 15.0)
    assert MAX_INPUT_BYTES == 4 * 1024 * 1024
    assert MAX_IMAGE_SIDE == 8192
    assert MAX_IMAGE_PIXELS == 24 * 1024 * 1024
