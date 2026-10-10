"""Generate private OCR benchmark predictions using a locally installed Tesseract binary.

This tool never treats OCR content as authoritative product identity. It only matches
OCR text against a separately provided, private allowlist of exact product codes.
"""

import argparse
import json
import os
import re
import subprocess
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import cast

from office_assistant.business.normalization import normalize_product_code
from office_assistant.scripts.benchmark_ocr import MAX_CASES, MAX_MANIFEST_BYTES, OcrPrediction

MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_REGISTRY_BYTES = 1024 * 1024
MAX_REGISTRY_CODES = 5_000
MAX_OCR_TEXT_CHARS = 1_000_000
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
LANGUAGE_PATTERN = re.compile(r"[A-Za-z]+(?:\+[A-Za-z]+)*\Z")


@dataclass(frozen=True, slots=True)
class ImageCase:
    query_id: str
    relative_path: str


def load_image_manifest(path: Path) -> list[ImageCase]:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Image manifest exceeds 16 MiB")
    result: list[ImageCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw: object = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("Invalid image manifest JSONL") from error
        if not isinstance(raw, dict):
            raise ValueError("Image manifest record must be an object")
        row = cast(dict[str, object], raw)
        if row.keys() != {"query_id", "image"}:
            raise ValueError("Image manifest fields must be query_id and image")
        query_id, image_name = row["query_id"], row["image"]
        if not isinstance(query_id, str) or not query_id.strip():
            raise ValueError("query_id must be nonempty")
        if not isinstance(image_name, str) or not image_name.strip():
            raise ValueError("image must be a nonempty relative filename")
        result.append(ImageCase(query_id.strip(), image_name))
        if len(result) > MAX_CASES:
            raise ValueError("Too many OCR benchmark images")
    if not result:
        raise ValueError("Image manifest is empty")
    if len({case.query_id for case in result}) != len(result):
        raise ValueError("Duplicate query_id in image manifest")
    return result


def resolve_private_image(root: Path, image_name: str) -> Path:
    """Never let a manifest select an absolute, traversal or symlink-escape path."""
    if (
        any(char in image_name for char in ("\\", ":", "\x00"))
        or any(part in ("", ".", "..") for part in image_name.split("/"))
        or Path(image_name).is_absolute()
    ):
        raise ValueError("Invalid relative image path")
    base = root.expanduser().resolve()
    if not base.is_dir():
        raise ValueError("Private image root is not a directory")
    path = (base / image_name).resolve()
    if not path.is_relative_to(base) or path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError("Image path is outside the approved root or has an invalid extension")
    if not path.is_file():
        raise ValueError("OCR image is missing")
    size = path.stat().st_size
    if not 0 < size <= MAX_IMAGE_BYTES:
        raise ValueError("OCR image is empty or exceeds 25 MiB")
    return path


def load_product_codes(path: Path) -> tuple[str, ...]:
    """Exact DB-exported code allowlist; never guess new identities from free OCR text."""
    if path.stat().st_size > MAX_REGISTRY_BYTES:
        raise ValueError("Product code allowlist exceeds 1 MiB")
    codes: list[str] = []
    for value in path.read_text(encoding="utf-8").splitlines():
        if not value.strip():
            continue
        if len(value) > 256:
            raise ValueError("Product code is too long")
        try:
            codes.append(normalize_product_code(value))
        except ValueError as error:
            raise ValueError("Invalid product code in allowlist") from error
        if len(codes) > MAX_REGISTRY_CODES:
            raise ValueError("Too many OCR product codes")
    if not codes or len(set(codes)) != len(codes):
        raise ValueError("Product code allowlist is empty or contains duplicate canonical codes")
    return tuple(codes)


def extract_known_codes(ocr_text: str, known_codes: tuple[str, ...]) -> tuple[str, ...]:
    """Match approved separator variants only; no fuzzy edits or substring matches."""
    normalized = unicodedata.normalize("NFKC", ocr_text)
    found: list[str] = []
    for code in known_codes:
        pattern = r"(?<!\w)" + r"[\s_-]*".join(re.escape(char) for char in code) + r"(?!\w)"
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            found.append(code)
    return tuple(found)


def read_tesseract(image: Path, executable: str, languages: str, timeout: int) -> str:
    if not LANGUAGE_PATTERN.fullmatch(languages) or len(languages) > 48:
        raise ValueError("Invalid Tesseract language selection")
    if not 1 <= timeout <= 120:
        raise ValueError("OCR timeout must be between 1 and 120 seconds")
    try:
        completed = subprocess.run(
            [executable, str(image), "stdout", "-l", languages, "--psm", "11"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Local OCR executable unavailable or timed out") from error
    if completed.returncode != 0:
        # Do not relay provider output: it can contain local paths and private text.
        raise ValueError("Local OCR engine failed")
    if len(completed.stdout) > MAX_OCR_TEXT_CHARS:
        raise ValueError("OCR output exceeds the safe text limit")
    return completed.stdout


def produce_predictions(
    image_root: Path,
    cases: list[ImageCase],
    codes: tuple[str, ...],
    read_image_text: Callable[[Path], str],
) -> list[OcrPrediction]:
    if not cases or len(cases) > MAX_CASES:
        raise ValueError("Invalid OCR image batch size")
    if len({case.query_id for case in cases}) != len(cases):
        raise ValueError("Duplicate query_id in OCR image batch")
    result: list[OcrPrediction] = []
    for case in cases:
        path = resolve_private_image(image_root, case.relative_path)
        text = read_image_text(path)
        if len(text) > MAX_OCR_TEXT_CHARS:
            raise ValueError("OCR output exceeds the safe text limit")
        result.append(OcrPrediction(case.query_id, extract_known_codes(text, codes)))
    return result


def write_private_predictions(path: Path, predictions: list[OcrPrediction]) -> None:
    """Write all predictions atomically; no partial output on an OCR failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            "w", dir=path.parent, prefix=".ocr-", encoding="utf-8", delete=False
        ) as stream:
            temporary = Path(stream.name)
            for prediction in predictions:
                stream.write(
                    json.dumps(
                        {
                            "query_id": prediction.query_id,
                            "extracted_codes": list(prediction.extracted_codes),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Tesseract on private benchmark images")
    parser.add_argument("--images", required=True, type=Path)
    parser.add_argument("--image-root", required=True, type=Path)
    parser.add_argument("--codes", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tesseract", default="tesseract")
    parser.add_argument("--languages", default="eng")
    parser.add_argument("--timeout-seconds", type=int, default=30)
    args = parser.parse_args()

    try:
        cases = load_image_manifest(args.images)
        known_codes = load_product_codes(args.codes)
        predictions = produce_predictions(
            args.image_root,
            cases,
            known_codes,
            lambda image: read_tesseract(
                image, args.tesseract, args.languages, args.timeout_seconds
            ),
        )
        write_private_predictions(args.output, predictions)
        print(
            json.dumps(
                {
                    "task": "local_ocr_candidate_generation",
                    "processed": len(predictions),
                    "recognized_image_count": sum(bool(row.extracted_codes) for row in predictions),
                    "image_manifest_sha256": sha256(args.images.read_bytes()).hexdigest(),
                    "code_allowlist_sha256": sha256(args.codes.read_bytes()).hexdigest(),
                },
                sort_keys=True,
            )
        )
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
