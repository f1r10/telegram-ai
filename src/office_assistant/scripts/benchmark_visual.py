"""Offline, reproducible Phase 12B ranking evaluation. Never opens pictures or calls providers."""

import argparse
import json
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast

from office_assistant.business.normalization import normalize_product_code

MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_CASES = 10_000
STATUSES = frozenset({"EXACT_MATCH", "PROBABLE_MATCH", "SIMILAR", "NO_MATCH"})
IDENTITY_CLAIMS = frozenset({"EXACT_MATCH", "PROBABLE_MATCH"})


@dataclass(frozen=True, slots=True)
class TruthCase:
    query_id: str
    scenario: str
    exact_code: str | None
    similar_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Prediction:
    query_id: str
    status: str
    ranked_codes: tuple[str, ...]


def _mapping(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise ValueError("JSONL records must be objects with string keys")
    return cast(dict[str, object], raw)


def _fields(record: dict[str, object], required: set[str]) -> None:
    if record.keys() != required:
        raise ValueError("JSONL record fields do not match the documented schema")


def _text(record: dict[str, object], key: str) -> str:
    raw = record[key]
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{key} must be a nonempty string")
    return raw.strip()


def _code(value: str) -> str:
    try:
        return normalize_product_code(value)
    except ValueError as error:
        raise ValueError("Invalid product code in benchmark manifest") from error


def _codes(record: dict[str, object], key: str) -> tuple[str, ...]:
    raw = record[key]
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"{key} must be a list of product-code strings")
    values = tuple(_code(item) for item in cast(list[str], raw))
    if len(set(values)) != len(values):
        raise ValueError(f"{key} has duplicate canonical product codes")
    return values


def _read_rows(path: Path) -> list[dict[str, object]]:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Benchmark manifest exceeds its size limit")
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw: object = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("Invalid JSONL benchmark manifest") from error
        rows.append(_mapping(raw))
        if len(rows) > MAX_CASES:
            raise ValueError("Benchmark manifest exceeds the case count limit")
    if not rows:
        raise ValueError("Benchmark manifest has no records")
    return rows


def load_truth(path: Path) -> list[TruthCase]:
    result: list[TruthCase] = []
    for row in _read_rows(path):
        _fields(row, {"query_id", "scenario", "exact_code", "similar_codes"})
        raw_exact = row["exact_code"]
        if raw_exact is not None and not isinstance(raw_exact, str):
            raise ValueError("exact_code must be a product-code string or null")
        exact = _code(raw_exact) if isinstance(raw_exact, str) else None
        similar = _codes(row, "similar_codes")
        if exact in similar:
            raise ValueError("Exact product cannot also be a similar-only label")
        result.append(TruthCase(_text(row, "query_id"), _text(row, "scenario"), exact, similar))
    _unique_query_ids(case.query_id for case in result)
    return result


def load_predictions(path: Path) -> list[Prediction]:
    result: list[Prediction] = []
    for row in _read_rows(path):
        _fields(row, {"query_id", "status", "ranked_codes"})
        status = _text(row, "status")
        if status not in STATUSES:
            raise ValueError("Unknown prediction status")
        ranked = _codes(row, "ranked_codes")
        if status != "NO_MATCH" and not ranked:
            raise ValueError("A positive prediction requires a ranked product")
        result.append(Prediction(_text(row, "query_id"), status, ranked))
    _unique_query_ids(prediction.query_id for prediction in result)
    return result


def _unique_query_ids(ids: Iterable[str]) -> None:
    values = list(ids)
    if len(set(values)) != len(values):
        raise ValueError("Duplicate query_id in benchmark manifest")


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def evaluate(
    truth: list[TruthCase], predictions: list[Prediction]
) -> dict[str, int | float | None]:
    _unique_query_ids(case.query_id for case in truth)
    _unique_query_ids(prediction.query_id for prediction in predictions)
    by_id = {prediction.query_id: prediction for prediction in predictions}
    if not truth or set(by_id) != {case.query_id for case in truth}:
        raise ValueError("Predictions must cover each ground-truth query exactly once")

    exact_cases = [case for case in truth if case.exact_code is not None]
    no_exact_cases = [case for case in truth if case.exact_code is None]
    similar_cases = [case for case in no_exact_cases if case.similar_codes]
    declared_exact = [prediction for prediction in predictions if prediction.status == "EXACT_MATCH"]

    def hits_at(k: int) -> int:
        return sum(
            case.exact_code in by_id[case.query_id].ranked_codes[:k] for case in exact_cases
        )

    reciprocal_rank_sum = 0.0
    for case in exact_cases:
        ranking = by_id[case.query_id].ranked_codes
        if case.exact_code in ranking:
            reciprocal_rank_sum += 1 / (ranking.index(case.exact_code) + 1)

    correct_exact_claims = sum(
        next(case.exact_code for case in truth if case.query_id == prediction.query_id)
        == prediction.ranked_codes[0]
        for prediction in declared_exact
    )
    false_identity_claims = sum(
        by_id[case.query_id].status in IDENTITY_CLAIMS for case in no_exact_cases
    )
    similar_hits = sum(
        bool(set(case.similar_codes).intersection(by_id[case.query_id].ranked_codes[:5]))
        for case in similar_cases
    )
    return {
        "queries": len(truth),
        "exact_queries": len(exact_cases),
        "no_exact_queries": len(no_exact_cases),
        "similar_only_queries": len(similar_cases),
        "declared_exact_queries": len(declared_exact),
        "recall_at_1": _ratio(hits_at(1), len(exact_cases)),
        "recall_at_3": _ratio(hits_at(3), len(exact_cases)),
        "recall_at_5": _ratio(hits_at(5), len(exact_cases)),
        "mrr": round(reciprocal_rank_sum / len(exact_cases), 4) if exact_cases else None,
        "exact_identity_precision": _ratio(correct_exact_claims, len(declared_exact)),
        "false_positive_rate": _ratio(false_identity_claims, len(no_exact_cases)),
        "similar_recall_at_5": _ratio(similar_hits, len(similar_cases)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline visual product ranking benchmark")
    parser.add_argument("--truth", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        truth = load_truth(args.truth)
        predictions = load_predictions(args.predictions)
        metrics = evaluate(truth, predictions)
        payload = {
            "schema_version": 1,
            "ground_truth_sha256": sha256(args.truth.read_bytes()).hexdigest(),
            "predictions_sha256": sha256(args.predictions.read_bytes()).hexdigest(),
            "metrics": metrics,
        }
        output = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n", encoding="utf-8")
        print(output)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
