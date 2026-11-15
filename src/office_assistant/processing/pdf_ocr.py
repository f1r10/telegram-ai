"""Bounded, local-only OCR of PDF pages without extractable text.

External binaries never receive shell input. Private page text and process stderr
are never logged. A damaged or slow page cannot block indexing of readable pages.

The Tesseract invocation itself sits behind the OCR seam (``global_intelligence.providers``,
Phase 2 gap G2); this module keeps only the rasterisation, the page/document budgets and
the reason vocabulary, all unchanged by the seam.
"""

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from office_assistant.global_intelligence.providers import (
    LANGUAGE_PATTERN,
    RECOGNITION_FAILED,
    TEXT_LIMIT,
    OCRProvider,
    OcrProviderError,
    TesseractOcrProvider,
)

MAX_RASTER_BYTES = 16 * 1024 * 1024

#: Provider failure codes translated into this module's unchanged reason vocabulary.
_PROVIDER_REASONS = {
    RECOGNITION_FAILED: "OCR_RECOGNITION_FAILED",
    TEXT_LIMIT: "OCR_TEXT_SIZE_LIMIT",
}


@dataclass(frozen=True, slots=True)
class PdfOcrConfig:
    languages: str = "aze+eng+rus"
    max_pages: int = 80
    page_timeout_seconds: float = 15.0
    document_timeout_seconds: float = 150.0
    max_dimension_pixels: int = 2000

    def __post_init__(self) -> None:
        if LANGUAGE_PATTERN.fullmatch(self.languages) is None:
            raise ValueError("Invalid PDF OCR language configuration")
        if not 1 <= self.max_pages <= 250:
            raise ValueError("Invalid PDF OCR page limit")
        if not 1 <= self.page_timeout_seconds <= 120:
            raise ValueError("Invalid PDF OCR page timeout")
        if not 1 <= self.document_timeout_seconds <= 240:
            raise ValueError("Invalid PDF OCR document timeout")
        if not 800 <= self.max_dimension_pixels <= 3000:
            raise ValueError("Invalid PDF OCR raster dimension")


@dataclass(frozen=True, slots=True)
class OcrPageResult:
    text: str = ""
    reason: str | None = None


class _OcrFailure(Exception):
    """Sanitized local engine failure; never includes document text or stderr."""


class PdfOcrService:
    def __init__(
        self, config: PdfOcrConfig | None = None, *, provider: OCRProvider | None = None
    ) -> None:
        self.config = config or PdfOcrConfig()
        # Tesseract is the only registered production provider; the seam exists so an
        # alternative engine is adopted by registration, not by editing this service.
        self.provider: OCRProvider = provider or TesseractOcrProvider()

    def session(self, path: Path) -> "PdfOcrSession":
        return PdfOcrSession(path, self.config, self.provider)


class PdfOcrSession:
    def __init__(self, path: Path, config: PdfOcrConfig, provider: OCRProvider) -> None:
        self.path = path.resolve()
        self.config = config
        self.provider = provider
        self._languages = tuple(config.languages.split("+"))
        self._deadline = time.monotonic() + config.document_timeout_seconds
        self.attempted_pages = 0
        self.recovered_pages = 0

    def _timeout(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise _OcrFailure("OCR_DOCUMENT_TIMEOUT")
        return min(self.config.page_timeout_seconds, remaining)

    def read_page(self, page_number: int) -> OcrPageResult:
        if page_number < 1:
            return OcrPageResult(reason="OCR_INVALID_PAGE")
        if self.attempted_pages >= self.config.max_pages:
            return OcrPageResult(reason="OCR_PAGE_LIMIT")
        if time.monotonic() >= self._deadline:
            return OcrPageResult(reason="OCR_DOCUMENT_TIMEOUT")
        self.attempted_pages += 1

        try:
            text = self._run_page(page_number).strip()
        except _OcrFailure as error:
            return OcrPageResult(reason=str(error))
        except subprocess.TimeoutExpired:
            return OcrPageResult(reason="OCR_PAGE_TIMEOUT")
        except FileNotFoundError:
            return OcrPageResult(reason="OCR_ENGINE_UNAVAILABLE")
        except OSError:
            return OcrPageResult(reason="OCR_LOCAL_IO_FAILURE")
        if not text:
            return OcrPageResult(reason="OCR_NO_TEXT")
        self.recovered_pages += 1
        return OcrPageResult(text=text)

    def _run_page(self, page_number: int) -> str:
        # TemporaryDirectory is private (mode 0700) and removes intermediate images.
        with TemporaryDirectory(prefix="office-pdf-ocr-") as temp_dir:
            prefix = Path(temp_dir) / "page"
            render = subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-singlefile",
                    "-scale-to",
                    str(self.config.max_dimension_pixels),
                    "-png",
                    str(self.path),
                    str(prefix),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self._timeout(),
                check=False,
            )
            raster = prefix.with_suffix(".png")
            if render.returncode != 0 or not raster.is_file():
                raise _OcrFailure("OCR_RENDER_FAILED")
            size = raster.stat().st_size
            if not 0 < size <= MAX_RASTER_BYTES:
                raise _OcrFailure("OCR_RASTER_SIZE_LIMIT")
            try:
                result = self.provider.recognize(
                    raster.read_bytes(),
                    languages=self._languages,
                    psm=3,
                    timeout_seconds=self._timeout(),
                )
            except OcrProviderError as error:
                raise _OcrFailure(
                    _PROVIDER_REASONS.get(str(error), "OCR_RECOGNITION_FAILED")
                ) from error
            return result.text
