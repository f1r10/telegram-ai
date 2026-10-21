"""Offline opt-in Docling PDF comparison; never alters business records or Qdrant."""

import argparse
import hashlib
import json
import os
import unicodedata
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from office_assistant.processing.parsers import PdfParser


_MAX_PDF_BYTES = 25 * 1024 * 1024
_MAX_EXTRACTED_CHARS = 8 * 1024 * 1024


class DoclingEvaluationError(RuntimeError):
    """Safe diagnostic label; never emit private source content or file paths."""


@dataclass(frozen=True, slots=True)
class Recall:
    samples: int
    native_hits: int
    docling_hits: int

    def result(self) -> dict[str, object]:
        return {
            "samples": self.samples,
            "native_phrase_recall": round(self.native_hits / self.samples, 4)
            if self.samples
            else None,
            "docling_phrase_recall": round(self.docling_hits / self.samples, 4)
            if self.samples
            else None,
        }


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def score_phrases(native: str, candidate: str, expected: list[str]) -> Recall:
    """Human verified expected text is never copied into result output."""
    source = normalize(native)
    proposed = normalize(candidate)
    phrases = [normalize(value) for value in expected]
    if not phrases or any(not item or len(item) > 250 for item in phrases):
        raise ValueError("Invalid private label set")
    return Recall(
        len(phrases),
        sum(item in source for item in phrases),
        sum(item in proposed for item in phrases),
    )


def docling_pdf(pdf_path: Path, artifacts_root: Path) -> str:
    """Use preprovisioned local Docling model artifacts and disable remote services."""
    if not artifacts_root.is_dir() or artifacts_root.is_symlink():
        raise DoclingEvaluationError("local_models_missing")
    if pdf_path.is_symlink() or not pdf_path.is_file() or pdf_path.suffix.casefold() != ".pdf":
        raise DoclingEvaluationError("invalid_pdf")
    if not 0 < pdf_path.stat().st_size <= _MAX_PDF_BYTES:
        raise DoclingEvaluationError("pdf_size_limit")
    try:
        with pdf_path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise DoclingEvaluationError("invalid_pdf_header")
    except OSError:
        raise DoclingEvaluationError("invalid_pdf") from None
    if os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1":
        raise DoclingEvaluationError("offline_environment_required")
    try:
        models = cast(Any, import_module("docling.datamodel.base_models"))
        options = cast(Any, import_module("docling.datamodel.pipeline_options"))
        module = cast(Any, import_module("docling.document_converter"))
        pipeline = options.PdfPipelineOptions(artifacts_path=artifacts_root)
        pipeline.do_ocr = False
        pipeline.do_table_structure = True
        pipeline.enable_remote_services = False
        pipeline.allow_external_plugins = False
        pdf_option = module.PdfFormatOption(pipeline_options=pipeline)
        converter = module.DocumentConverter(format_options={models.InputFormat.PDF: pdf_option})
        output = converter.convert(str(pdf_path)).document.export_to_markdown()
        if not isinstance(output, str):
            raise DoclingEvaluationError("unsupported_output")
        if len(output) > _MAX_EXTRACTED_CHARS:
            raise DoclingEvaluationError("extracted_text_limit")
        return output
    except (ModuleNotFoundError, ImportError):
        raise DoclingEvaluationError("docling_not_installed") from None
    except DoclingEvaluationError:
        raise
    except Exception:
        raise DoclingEvaluationError("docling_conversion_failed") from None


def main() -> None:
    parser = argparse.ArgumentParser(description="Private PDF parser comparison")
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--local-models", type=Path, required=True)
    parser.add_argument("--evaluate", action="store_true")
    args = parser.parse_args()
    if args.pdf.is_symlink() or not args.pdf.is_file() or args.pdf.suffix.casefold() != ".pdf":
        raise SystemExit("Invalid PDF input")
    if not 0 < args.pdf.stat().st_size <= 25 * 1024 * 1024:
        raise SystemExit("PDF is outside size limit")
    if args.truth.is_symlink() or not args.truth.is_file() or args.truth.stat().st_size > 64 * 1024:
        raise SystemExit("Missing or oversized private labels")
    try:
        content = args.truth.read_bytes()
        raw: object = json.loads(content)
        if not isinstance(raw, list):
            raise ValueError("Invalid labels")
        items = cast(list[object], raw)
        if not 1 <= len(items) <= 500 or not all(isinstance(item, str) for item in items):
            raise ValueError("Invalid labels")
        labels = cast(list[str], items)
        if any(not normalize(item) or len(item) > 250 for item in labels):
            raise ValueError("Invalid labels")
    except (ValueError, UnicodeError):
        raise SystemExit("Invalid private label file") from None
    digest = hashlib.sha256(content).hexdigest()
    if not args.evaluate:
        print(json.dumps({"status": "DRY_RUN", "samples": len(labels), "truth_sha256": digest}))
        return
    try:
        baseline = PdfParser().parse(args.pdf, source_file_id=0, media_type="application/pdf")
        proposed = docling_pdf(args.pdf, args.local_models)
        scores = score_phrases(baseline.text(), proposed, labels).result()
    except (DoclingEvaluationError, ValueError):
        print(json.dumps({"status": "UNAVAILABLE", "content_disclosed": False}))
        raise SystemExit(2) from None
    print(json.dumps({"status": "EVALUATED", "truth_sha256": digest, **scores}))


if __name__ == "__main__":
    main()
