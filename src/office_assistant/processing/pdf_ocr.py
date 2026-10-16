"""Bounded, local-only OCR of PDF pages without extractable text.

External binaries never receive shell input. Private page text and process stderr
are never logged. A damaged or slow page cannot block indexing of readable pages.
"""

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile

_LANGUAGES = re.compile(r"[a-z]{3}(?:\+[a-z]{3})*\Z")
MAX_RASTER_BYTES = 16 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class PdfOcrConfig:
    languages: str = "aze+eng+rus"
    max_pages: int = 80
    page_timeout_seconds: float = 15.0
    document_timeout_seconds: float = 150.0
    max_dimension_pixels: int = 2000

    def __post_init__(self) -> None:
        if _LANGUAGES.fullmatch(self.languages) is None:
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
    def __init__(self, config: PdfOcrConfig | None = None) -> None:
        self.config = config or PdfOcrConfig()

    def session(self, path: Path) -> "PdfOcrSession":
        return PdfOcrSession(path, self.config)


class PdfOcrSession:
    def __init__(self, path: Path, config: PdfOcrConfig) -> None:
        self.path = path.resolve()
        self.config = config
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
                    "-f", str(page_number),
                    "-l", str(page_number),
                    "-singlefile",
                    "-scale-to", str(self.config.max_dimension_pixels),
                    "-png",
                    str(self.path), str(prefix),
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
            # A disk-backed pipe avoids holding unbounded subprocess stdout in memory.
            with TemporaryFile(mode="w+b") as output:
                result = subprocess.run(
                    [
                        "tesseract", str(raster), "stdout",
                        "-l", self.config.languages, "--psm", "3",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=self._timeout(),
                    check=False,
                )
                if result.returncode != 0:
                    raise _OcrFailure("OCR_RECOGNITION_FAILED")
                if output.tell() > MAX_TEXT_BYTES:
                    raise _OcrFailure("OCR_TEXT_SIZE_LIMIT")
                output.seek(0)
                return output.read(MAX_TEXT_BYTES + 1).decode("utf-8", errors="replace")
