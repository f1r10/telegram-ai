"""OCR engine seam (Phase 2 layer 2, audit gap G2).

Tesseract is the only registered provider in Phase 2: the seam exists so an alternative engine
can be benchmarked and adopted by *registration* rather than by editing the OCR services
(ARCHITECTURE §5). Behaviour is unchanged from the pre-Phase-2 services — the same argument
vector, the same recognised-text cap and the same three-letter language-list syntax.

ASSUMPTION: ``recognize`` takes ``timeout_seconds`` explicitly. ARCHITECTURE §5 sketches the
signature without it, but the PDF path computes a per-page budget (``min(page timeout, remaining
document budget)``); moving the invocation behind the seam without carrying that value would
silently change the document deadline. See docs/OPEN-QUESTIONS.md Q55.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from typing import NamedTuple, Protocol

#: Language-list syntax accepted on the command line, e.g. ``aze+eng+rus`` (unchanged regex).
LANGUAGE_PATTERN = re.compile(r"[a-z]{3}(?:\+[a-z]{3})*\Z")
#: Recognised-text cap handed back to a caller (unchanged: 1 MiB).
MAX_TEXT_BYTES = 1024 * 1024
#: Engine version recorded on a result. The deployed binary is *not* probed at runtime: the
#: bounded-OCR contract forbids spawning a process before a caller asks for recognition.
TESSERACT_VERSION = "unknown"

#: Sanitized provider failure codes. Each OCR service maps them to its own reason vocabulary.
RECOGNITION_FAILED = "RECOGNITION_FAILED"
TEXT_LIMIT = "TEXT_LIMIT"


class OcrResult(NamedTuple):
    """One recognised image. Text is returned exactly as the engine produced it."""

    text: str
    confidence: float | None
    engine: str
    version: str
    languages: tuple[str, ...]
    warnings: tuple[str, ...]


class OcrProviderError(Exception):
    """Sanitized OCR engine failure. Never carries document text or process stderr."""


class OCRProvider(Protocol):
    """An OCR engine behind a seam. Tesseract is the only registered implementation today."""

    name: str
    version: str
    supported_languages: frozenset[str]

    def recognize(
        self,
        image: bytes,
        *,
        languages: tuple[str, ...],
        psm: int,
        timeout_seconds: float,
    ) -> OcrResult: ...


class TesseractOcrProvider:
    """Local Tesseract OCR of untrusted image bytes.

    The bytes never leave the process (ADR 0017 §2). They are handed to the engine as a private
    temporary file and its stdout is captured through a disk-backed pipe, so a hostile image
    cannot turn recognition into an unbounded in-memory read.
    """

    name = "tesseract"
    version = TESSERACT_VERSION
    #: Languages installed in the deployment image (Dockerfile: aze, eng, rus). Declared, not
    #: probed, so the capability is visible without running the engine.
    supported_languages = frozenset({"aze", "eng", "rus"})

    def recognize(
        self,
        image: bytes,
        *,
        languages: tuple[str, ...],
        psm: int,
        timeout_seconds: float,
    ) -> OcrResult:
        with TemporaryDirectory(prefix="office-ocr-") as temp_dir:
            source = Path(temp_dir) / "page"
            source.write_bytes(image)
            # A disk-backed pipe avoids holding unbounded subprocess stdout in memory.
            with TemporaryFile(mode="w+b") as output:
                result = subprocess.run(
                    [
                        "tesseract",
                        str(source),
                        "stdout",
                        "-l",
                        "+".join(languages),
                        "--psm",
                        str(psm),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=timeout_seconds,
                    check=False,
                )
                if result.returncode != 0:
                    raise OcrProviderError(RECOGNITION_FAILED)
                if output.tell() > MAX_TEXT_BYTES:
                    raise OcrProviderError(TEXT_LIMIT)
                output.seek(0)
                text = output.read(MAX_TEXT_BYTES + 1).decode("utf-8", errors="replace")
        return OcrResult(
            text=text,
            confidence=None,
            engine=self.name,
            version=self.version,
            languages=languages,
            warnings=(),
        )
