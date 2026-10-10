"""Evaluate local OCR code extraction against private, manually annotated JSONL manifests.

This script does not execute OCR, contact providers, or disclose per-image labels in output.
"""

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from office_assistant.business.normalization import normalize_product_code

MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_CASES = 10_000
SCENARIOS = frozenset(
    {
        "different_angle",
        "different_background",
        "phone_photo",
        "weak_light",
        "crop",
        "packaging",
        "similar_models",
        "partial_model_code",
        "pdf_screenshot",
        "catalog_vs_photo",
        "unrelated_item",
    }
)


@dataclass(frozen=True, slots=True)
class OcrTruth:
    query_id: str
    scenario: str
    visible_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OcrPrediction:
    query_id: str
    extracted_codes: tuple[str, ...]


def _unique_records(records: list[OcrTruth] | list[OcrPrediction]) -> None:
    ids = [record.query_id for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate query_id in OCR benchmark manifest")


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Repeated JSON field in OCR benchmark manifest")
        value[key] = item
    return value


def _read_rows(path: Path) -> list[dict[str, object]]:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("OCR benchmark manifest exceeds 16 MiB")
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw: object = json.loads(line, object_pairs_hook=_no_duplicate_keys)
        except json.JSONDecodeError as error:
            raise ValueError("Invalid OCR JSONL manifest") from error
        if not isinstance(raw, dict):
            raise ValueError("OCR JSONL records must be objects")
        row = cast(dict[str, object], raw)
        rows.append(row)
        if len(rows) > MAX_CASES:
            raise ValueError("OCR benchmark exceeds 10,000 cases")
    if not rows:
        raise ValueError("OCR benchmark manifest has no cases")
    return rows


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text")
    return value.strip()


def _canonical_codes(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of product codes")
    items = cast(list[object], value)
    if not all(isinstance(item, str) for item in items):
        raise ValueError(f"{field} must contain product code strings")
    codes: list[str] = []
    for item in cast(list[str], items):
        try:
            codes.append(normalize_product_code(item))
        except ValueError as error:
            raise ValueError(f"{field} includes an invalid product code") from error
    if len(codes) != len(set(codes)):
        raise ValueError(f"{field} has duplicate canonical product codes")
    return tuple(codes)


def load_ocr_truth(path: Path) -> list[OcrTruth]:
    result: list[OcrTruth] = []
    for row in _read_rows(path):
        if row.keys() != {"query_id", "scenario", "visible_codes"}:
            raise ValueError("OCR ground truth must have exact documented fields")
        scenario = _require_text(row["scenario"], "scenario")
        if scenario not in SCENARIOS:
            raise ValueError("Unknown OCR benchmark scenario")
        result.append(
            OcrTruth(
                _require_text(row["query_id"], "query_id"),
                scenario,
                _canonical_codes(row["visible_codes"], "visible_codes"),
            )
        )
    _unique_records(result)
    return result


def load_ocr_predictions(path: Path) -> list[OcrPrediction]:
    result: list[OcrPrediction] = []
    for row in _read_rows(path):
        if row.keys() != {"query_id", "extracted_codes"}:
            raise ValueError("OCR predictions must have exact documented fields")
        result.append(
            OcrPrediction(
                _require_text(row["query_id"], "query_id"),
                _canonical_codes(row["extracted_codes"], "extracted_codes"),
            )
        )
    _unique_records(result)
    return result


def _ratio(part: int, total: int) -> float | None:
    return round(part / total, 4) if total else None


def _metrics(
    truth: list[OcrTruth], by_id: dict[str, OcrPrediction]
) -> dict[str, int | float | None]:
    true_positive_codes = 0
    total_visible_codes = 0
    total_extracted_codes = 0
    exact_sets = 0
    with_visible = 0
    image_hits = 0
    no_visible = 0
    false_alarms = 0
    for case in truth:
        visible = set(case.visible_codes)
        extracted = set(by_id[case.query_id].extracted_codes)
        hits = len(visible & extracted)
        true_positive_codes += hits
        total_visible_codes += len(visible)
        total_extracted_codes += len(extracted)
        exact_sets += visible == extracted
        if visible:
            with_visible += 1
            image_hits += hits > 0
        else:
            no_visible += 1
            false_alarms += bool(extracted)

    precision = _ratio(true_positive_codes, total_extracted_codes)
    recall = _ratio(true_positive_codes, total_visible_codes)
    f1 = _ratio(2 * true_positive_codes, total_extracted_codes + total_visible_codes)
    return {
        "queries": len(truth),
        "images_with_visible_codes": with_visible,
        "images_without_visible_codes": no_visible,
        "visible_code_count": total_visible_codes,
        "extracted_code_count": total_extracted_codes,
        "correct_code_count": true_positive_codes,
        "code_precision": precision,
        "code_recall": recall,
        "code_f1": f1,
        "query_exact_set_accuracy": _ratio(exact_sets, len(truth)),
        "image_code_hit_rate": _ratio(image_hits, with_visible),
        "false_alarm_image_rate": _ratio(false_alarms, no_visible),
    }


def evaluate_ocr(truth: list[OcrTruth], predictions: list[OcrPrediction]) -> dict[str, object]:
    _unique_records(truth)
    _unique_records(predictions)
    by_id = {prediction.query_id: prediction for prediction in predictions}
    if not truth or set(by_id) != {case.query_id for case in truth}:
        raise ValueError("OCR predictions must cover all and only ground-truth query IDs")

    scenarios: dict[str, list[OcrTruth]] = defaultdict(list)
    for case in truth:
        scenarios[case.scenario].append(case)

    return {
        "overall": _metrics(truth, by_id),
        "by_scenario": {
            scenario: _metrics(scenarios[scenario], by_id) for scenario in sorted(scenarios)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate offline OCR product-code extraction")
    parser.add_argument("--truth", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        truth = load_ocr_truth(args.truth)
        predictions = load_ocr_predictions(args.predictions)
        payload = {
            "schema_version": 1,
            "task": "ocr_code_extraction",
            "ground_truth_sha256": sha256(args.truth.read_bytes()).hexdigest(),
            "predictions_sha256": sha256(args.predictions.read_bytes()).hexdigest(),
            "metrics": evaluate_ocr(truth, predictions),
        }
        output = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n", encoding="utf-8")
        print(output)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
