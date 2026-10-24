"""Private, bounded Tesseract OCR for uploaded PNG/JPEG images.

No model API, prompt or vendor transport. A failed image is deferred rather than
being indexed as a successful document with zero searchable content.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile

_LANGUAGES = re.compile(r"[a-z]{3}(?:\+[a-z]{3})*\Z")
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
MAX_IMAGE_SIDE = 8192
MAX_IMAGE_PIXELS = 24 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ImageOcrResult:
    text: str = ""
    reason: str | None = None


class ImageOcrService:
    """Read printed text from locally stored, type-checked raster files."""

    def __init__(self, *, languages: str = "aze+eng+rus", timeout_seconds: float = 15.0) -> None:
        if _LANGUAGES.fullmatch(languages) is None:
            raise ValueError("Invalid image OCR language configuration")
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("Invalid image OCR timeout")
        self.languages = languages
        self.timeout_seconds = timeout_seconds

    def read(self, path: Path, *, media_type: str) -> ImageOcrResult:
        if media_type not in {"image/png", "image/jpeg"}:
            return ImageOcrResult(reason="IMAGE_OCR_MEDIA_UNSUPPORTED")
        try:
            size = path.stat().st_size
            if size <= 0 or size > MAX_INPUT_BYTES or path.is_symlink():
                return ImageOcrResult(reason="IMAGE_OCR_SIZE_LIMIT")
            # PNG IHDR dimensions are readily accessible without decoding the image.
            if media_type == "image/png":
                with path.open("rb") as source:
                    header = source.read(24)
                if len(header) != 24 or header[12:16] != b"IHDR":
                    return ImageOcrResult(reason="IMAGE_OCR_INVALID_HEADER")
                width = int.from_bytes(header[16:20], "big")
                height = int.from_bytes(header[20:24], "big")
                if (
                    not width
                    or not height
                    or width > MAX_IMAGE_SIDE
                    or height > MAX_IMAGE_SIDE
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    return ImageOcrResult(reason="IMAGE_OCR_PIXEL_LIMIT")
            with TemporaryFile(mode="w+b") as output:
                result = subprocess.run(
                    [
                        "tesseract",
                        str(path),
                        "stdout",
                        "-l",
                        self.languages,
                        "--psm",
                        "6",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                if result.returncode != 0:
                    return ImageOcrResult(reason="IMAGE_OCR_RECOGNITION_FAILED")
                if output.tell() > MAX_TEXT_BYTES:
                    return ImageOcrResult(reason="IMAGE_OCR_TEXT_LIMIT")
                output.seek(0)
                text = output.read(MAX_TEXT_BYTES + 1).decode("utf-8", errors="replace").strip()
                if not text:
                    return ImageOcrResult(reason="IMAGE_OCR_NO_TEXT")
                return ImageOcrResult(text=text)
        except subprocess.TimeoutExpired:
            return ImageOcrResult(reason="IMAGE_OCR_TIMEOUT")
        except FileNotFoundError:
            return ImageOcrResult(reason="IMAGE_OCR_ENGINE_UNAVAILABLE")
        except OSError:
            return ImageOcrResult(reason="IMAGE_OCR_LOCAL_IO_FAILURE")
