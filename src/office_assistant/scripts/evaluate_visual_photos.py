"""Offline evaluation of real phone photos against approved active product references.

Never send pictures to a provider. No query vectors are persisted, and reports
contain aggregated statistics and manifest hashes, not filenames or product codes.

Dry-run verifies manifest and reference coverage without running a local model.
--apply requires a previously provisioned local CLIP or DINOv2 model.
Measured per-query local processing time excludes Telegram download and DB loading.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from dataclasses import dataclass
from hashlib import sha256
from math import ceil, isfinite
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import cast

from office_assistant.business.normalization import normalize_product_code
from office_assistant.core.settings import Settings, load_settings
from office_assistant.processing.image_similarity import (
    MAX_IMAGE_BYTES,
    VisualCandidate,
    fingerprint,
    shortlist,
)
from office_assistant.processing.semantic_visual import (
    SemanticReference,
    SemanticVisualSearch,
    balanced_references,
)
from office_assistant.scripts.benchmark_visual import (
    Prediction,
    TruthCase,
    evaluate,
)
from office_assistant.scripts.prepare_visual_references import approved_references

MAX_CASES = 1000
MAX_MANIFEST_BYTES = 1024 * 1024
_NAME = re.compile(r"[a-zA-Z0-9_.-]{1,80}\Z")


@dataclass(frozen=True, slots=True)
class PhotoCase:
    truth: TruthCase
    photo: Path


@dataclass(frozen=True, slots=True)
class PhotoEvaluation:
    predictions: tuple[Prediction, ...]
    totals: dict[str, int | float | None]
    scenarios: dict[str, dict[str, int | float | None]]
    unavailable: bool
    incomplete: bool
    latency_ms: dict[str, int | float | None]


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def summarize_latency_ms(samples: list[float]) -> dict[str, int | float | None]:
    """Aggregate only, using nearest-rank p95 (never expose per-photo identifiers)."""

    if any(not isfinite(value) or value < 0 for value in samples):
        raise ValueError("Invalid local photo timing sample")
    if not samples:
        return {"samples": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    ordered = sorted(samples)
    return {
        "samples": len(ordered),
        "p50_ms": round(float(median(ordered)), 1),
        "p95_ms": round(ordered[ceil(0.95 * len(ordered)) - 1], 1),
        "max_ms": round(ordered[-1], 1),
    }


def read_photo_manifest(path: Path, *, photo_root: Path) -> list[PhotoCase]:
    """Reject ambiguous labels, traversal, symlink inputs and oversized manifests."""

    if not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Photo manifest is missing or too large")
    root = photo_root.resolve(strict=True)
    results: list[PhotoCase] = []
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw: object = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("Photo manifest is not valid JSONL") from error
        if not isinstance(raw, dict):
            raise ValueError("Photo case must be a JSON object")
        data = cast(dict[str, object], raw)
        if set(data) != {"query_id", "scenario", "photo", "exact_code", "similar_codes"}:
            raise ValueError("Photo manifest fields do not match schema")
        query_id, scenario, filename = (data[k] for k in ("query_id", "scenario", "photo"))
        if (
            not isinstance(query_id, str)
            or not _NAME.fullmatch(query_id)
            or query_id in ids
            or not isinstance(scenario, str)
            or not _NAME.fullmatch(scenario)
            or not isinstance(filename, str)
            or not 1 <= len(filename) <= 240
        ):
            raise ValueError("Invalid photo ID, scenario or duplicate case")
        relative = Path(filename)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"..", "."} for part in relative.parts)
            or relative.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}
        ):
            raise ValueError("Invalid photo path")
        photo = root / relative
        resolved = photo.resolve(strict=True)
        if resolved != photo or not resolved.is_relative_to(root):
            raise ValueError("Photo paths must not contain symlinks or leave data root")
        if not resolved.is_file() or not 0 < resolved.stat().st_size <= MAX_IMAGE_BYTES:
            raise ValueError("Photo is empty or exceeds maximum size")
        raw_exact = data["exact_code"]
        if raw_exact is not None and not isinstance(raw_exact, str):
            raise ValueError("Exact label must be a code or null")
        exact = normalize_product_code(raw_exact) if isinstance(raw_exact, str) else None
        raw_similar = data["similar_codes"]
        if not isinstance(raw_similar, list):
            raise ValueError("Similar labels must be code strings")
        similar_items = cast(list[object], raw_similar)
        if not all(isinstance(item, str) for item in similar_items):
            raise ValueError("Similar labels must be code strings")
        codes = tuple(normalize_product_code(code) for code in cast(list[str], similar_items))
        if len(codes) != len(set(codes)) or exact in codes:
            raise ValueError("Similar labels must be unique and distinct from exact")
        ids.add(query_id)
        results.append(
            PhotoCase(
                truth=TruthCase(query_id, scenario, exact, codes),
                photo=resolved,
            )
        )
        if len(results) > MAX_CASES:
            raise ValueError("Too many photo cases")
    if not results:
        raise ValueError("At least one real photo is required")
    return results


def _validated_reference(root: Path, reference: SemanticReference) -> VisualCandidate | None:
    """An approved DB relation is insufficient if its underlying bytes changed."""

    try:
        path = (root / reference.storage_key).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            return None
        if not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
            return None
        if sha256(path.read_bytes()).hexdigest() != reference.content_sha256:
            return None
    except (OSError, ValueError):
        return None
    return VisualCandidate(reference.product_code, reference.storage_key)


def evaluate_photos(
    cases: list[PhotoCase],
    references: list[SemanticReference],
    *,
    storage_root: Path,
    semantic: SemanticVisualSearch | None,
    max_references: int = 80,
) -> PhotoEvaluation:
    if not cases or not 1 <= max_references <= 500:
        raise ValueError("Invalid evaluation size")
    root = storage_root.resolve(strict=True)
    selected = balanced_references(references, limit=max_references)
    valid = [
        (reference, candidate)
        for reference in selected
        if (candidate := _validated_reference(root, reference)) is not None
    ]
    refs = [item[0] for item in valid]
    near_refs = [item[1] for item in valid]
    incomplete = len(references) > len(selected) or len(valid) != len(selected)
    outcomes: list[Prediction] = []
    unavailable = False
    timings_ms: list[float] = []
    for case in cases:
        started = perf_counter()
        try:
            data = case.photo.read_bytes()
            if fingerprint(data) is None:
                # Invalid input cannot be counted as correct abstention in a
                # commercial recognition test: the dataset must be fixed.
                raise ValueError("A ground-truth photo is not a valid textured image")
            digest = sha256(data).hexdigest()
            exact_codes = sorted({r.product_code for r in refs if r.content_sha256 == digest})
            if exact_codes:
                # Same SHA is authoritative only because original bytes and DB
                # explicit confirmation were BOTH rechecked above.
                outcomes.append(Prediction(case.truth.query_id, "EXACT_MATCH", tuple(exact_codes)))
                continue
            near = shortlist(data, near_refs, root, max_results=5)
            if near:
                outcomes.append(
                    Prediction(case.truth.query_id, "SIMILAR", tuple(x.product_code for x in near))
                )
                continue
            if semantic is None:
                outcomes.append(Prediction(case.truth.query_id, "NO_MATCH", ()))
                continue
            found = semantic.search(data, refs, limit=5)
            if found.unavailable:
                unavailable = True
            if found.incomplete:
                incomplete = True
            codes = tuple(item.product_code for item in found.candidates)
            outcomes.append(Prediction(case.truth.query_id, "SIMILAR" if codes else "NO_MATCH", codes))
        finally:
            timings_ms.append((perf_counter() - started) * 1000)
    totals = evaluate([case.truth for case in cases], outcomes)
    cases_by_id = {case.truth.query_id: case.truth for case in cases}
    negative_cases = [case for case in cases if case.truth.exact_code is None]
    totals["negative_candidate_rate"] = _ratio(
        sum(
            bool(row.ranked_codes)
            for row in outcomes
            if cases_by_id[row.query_id].exact_code is None
        ),
        len(negative_cases),
    )
    scenarios: dict[str, dict[str, int | float | None]] = {}
    predictions = {row.query_id: row for row in outcomes}
    for scenario in sorted({case.truth.scenario for case in cases}):
        subset = [case.truth for case in cases if case.truth.scenario == scenario]
        positives = [item for item in subset if item.exact_code is not None]
        negatives = [item for item in subset if item.exact_code is None]
        scenarios[scenario] = {
            "queries": len(subset),
            "positive_queries": len(positives),
            "negative_queries": len(negatives),
            "top1_recall": _ratio(
                sum(
                    item.exact_code in predictions[item.query_id].ranked_codes[:1]
                    for item in positives
                ),
                len(positives),
            ),
            "negative_candidate_rate": _ratio(
                sum(bool(predictions[item.query_id].ranked_codes) for item in negatives),
                len(negatives),
            ),
        }
    return PhotoEvaluation(
        tuple(outcomes), totals, scenarios, unavailable, incomplete,
        summarize_latency_ms(timings_ms),
    )


async def run_evaluation(
    settings: Settings,
    *,
    manifest: Path,
    photo_root: Path,
    apply: bool,
    limit: int,
) -> dict[str, object]:
    if not 1 <= limit <= 500:
        raise ValueError("Reference limit out of range")
    cases = read_photo_manifest(manifest, photo_root=photo_root)
    refs = await approved_references(settings, limit=limit)
    if not apply:
        return {
            "status": "DRY_RUN",
            "cases": len(cases),
            "references": min(limit, len(refs)),
            "truncated": len(refs) > limit,
            "manifest_sha256": sha256(manifest.read_bytes()).hexdigest(),
        }
    storage = Path(settings.file_storage_path)
    semantic = SemanticVisualSearch(
        storage_root=storage,
        model_cache_root=Path(settings.embedding_cache_path),
        min_score=settings.visual_semantic_min_score,
        max_references=limit,
        version=settings.visual_semantic_model_version,
    )
    result = await asyncio.to_thread(
        evaluate_photos, cases, refs, storage_root=storage, semantic=semantic, max_references=limit
    )
    if result.unavailable:
        raise RuntimeError("Offline visual model unavailable; cannot report calibration as passed")
    return {
        "status": "EVALUATED",
        "cases": len(cases),
        "references": min(limit, len(refs)),
        "truncated": result.incomplete,
        "manifest_sha256": sha256(manifest.read_bytes()).hexdigest(),
        "metrics": result.totals,
        "by_scenario": result.scenarios,
        "local_latency_ms": result.latency_ms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--photo-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--apply", action="store_true", help="Evaluate using local offline model")
    args = parser.parse_args()
    logging.disable(logging.WARNING)
    try:
        result = asyncio.run(
            run_evaluation(
                load_settings(),
                manifest=args.manifest,
                photo_root=args.photo_root,
                apply=args.apply,
                limit=args.limit,
            )
        )
    except (OSError, ValueError, RuntimeError) as error:
        # Do not echo private manifest rows, filesystem paths or product codes.
        parser.exit(2, f"VISUAL_EVALUATION_FAILED error_type={type(error).__name__}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
