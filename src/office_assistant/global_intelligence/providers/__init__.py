"""Phase 2 engine seams. Tesseract is the only registered OCR provider today."""

from __future__ import annotations

from .ocr import (
    LANGUAGE_PATTERN,
    MAX_TEXT_BYTES,
    RECOGNITION_FAILED,
    TESSERACT_VERSION,
    TEXT_LIMIT,
    OCRProvider,
    OcrProviderError,
    OcrResult,
    TesseractOcrProvider,
)

__all__ = [
    "LANGUAGE_PATTERN",
    "MAX_TEXT_BYTES",
    "OCRProvider",
    "OcrProviderError",
    "OcrResult",
    "RECOGNITION_FAILED",
    "TESSERACT_VERSION",
    "TEXT_LIMIT",
    "TesseractOcrProvider",
]
