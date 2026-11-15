"""Metric primitives for the S17.3 evaluation table.

All functions are pure and offline. ``docs/evaluation/BENCHMARK_RESULTS.md`` is generated from these
numbers, so each metric documents its degenerate case explicitly rather than guessing.
"""

from __future__ import annotations

import math
import unicodedata
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

CONFIRMED = "confirmed"
PROBABLE = "probable"
UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class HarnessStatement:
    """One statement of a candidate answer, with the evidence the answer claims for it."""

    text: str
    status: str
    evidence_id: str | None = None
    locator: Mapping[str, str] | None = None

    def is_well_supported_confirmed(self) -> bool:
        """True when a ``confirmed`` statement carries both an evidence id and a locator.

        Task brief S13.1: evidence without a known source/locator can never be shown as confirmed.
        """

        return self.status == CONFIRMED and bool(self.evidence_id) and bool(self.locator)


def recall_at_k(relevant: Collection[str], retrieved: Sequence[str], k: int) -> float:
    """Fraction of the relevant documents found in the first ``k`` retrieved items.

    Raises ValueError when ``relevant`` is empty: recall against an empty ground-truth set is
    undefined, and silently returning 0.0 or 1.0 would corrupt the benchmark table.
    """

    if not relevant:
        raise ValueError("recall_at_k is undefined without relevant documents")
    if k < 1:
        raise ValueError("k must be at least 1")
    found = len(set(retrieved[:k]) & set(relevant))
    return found / len(set(relevant))


def reciprocal_rank(relevant: Collection[str], retrieved: Sequence[str]) -> float:
    """1 / rank of the first relevant item, or 0.0 when nothing relevant was retrieved."""

    relevant_set = set(relevant)
    for position, item in enumerate(retrieved, start=1):
        if item in relevant_set:
            return 1.0 / position
    return 0.0


def ndcg_at_k(relevant: Collection[str], retrieved: Sequence[str], k: int) -> float:
    """Normalized discounted cumulative gain with binary relevance."""

    if not relevant:
        raise ValueError("ndcg_at_k is undefined without relevant documents")
    if k < 1:
        raise ValueError("k must be at least 1")
    relevant_set = set(relevant)
    dcg = sum(
        1.0 / math.log2(position + 1)
        for position, item in enumerate(retrieved[:k], start=1)
        if item in relevant_set
    )
    ideal_hits = min(len(relevant_set), k)
    ideal = sum(1.0 / math.log2(position + 1) for position in range(1, ideal_hits + 1))
    return dcg / ideal if ideal else 0.0


def citation_accuracy(
    cited_evidence_ids: Sequence[str], valid_evidence_ids: Collection[str]
) -> float:
    """Fraction of cited evidence ids that really exist in the evidence bundle.

    Returns 1.0 when nothing is cited, because "no citation" is not a *wrong* citation; it is
    measured separately by ``citation_presence``.
    """

    if not cited_evidence_ids:
        return 1.0
    valid = set(valid_evidence_ids)
    return sum(1 for item in cited_evidence_ids if item in valid) / len(cited_evidence_ids)


def citation_presence(cited_evidence_ids: Sequence[str]) -> float:
    """1.0 when the answer cites at least one source, else 0.0."""

    return 1.0 if cited_evidence_ids else 0.0


def unsupported_confirmed_rate(statements: Sequence[HarnessStatement]) -> float:
    """Hallucination proxy: share of ``confirmed`` statements that lack source or locator.

    Returns 0.0 for an answer with no statements. That is deliberate: the metric measures what was
    asserted, and an empty answer asserts nothing.
    """

    confirmed = [item for item in statements if item.status == CONFIRMED]
    if not confirmed:
        return 0.0
    unsupported = sum(1 for item in confirmed if not item.is_well_supported_confirmed())
    return unsupported / len(confirmed)


def label_coverage(statements: Sequence[HarnessStatement]) -> float:
    """Share of statements carrying one of the three allowed epistemic labels."""

    if not statements:
        return 0.0
    allowed = {CONFIRMED, PROBABLE, UNKNOWN}
    return sum(1 for item in statements if item.status in allowed) / len(statements)


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean; 0.0 for an empty sequence (used when a metric is not applicable)."""

    return sum(values) / len(values) if values else 0.0


def _ratio(part: int, total: int) -> float | None:
    """Rounded ratio, or ``None`` when the denominator is empty (Phase-2 metric convention).

    Mirrors ``scripts/benchmark_ocr.py``: a degenerate denominator reports ``None`` rather than a
    misleading 0.0 or 1.0.
    """

    return round(part / total, 4) if total else None


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def score_table_cells(
    truth: Mapping[tuple[int, int], str],
    predicted: Mapping[tuple[int, int], str],
) -> dict[str, int | float | None]:
    """Cell-level precision/recall/F1 keyed by the ``(row, column)`` a cell was recovered at.

    A cell counts as correct only when the same key exists in both maps *and* its text matches
    after NFKC + casefold + whitespace normalization — position is part of table truth, so a right
    value in the wrong cell is not a hit. Consumption is **report only**: TEST_STRATEGY.md §5 fixes
    no accuracy threshold in Phase 2.
    """

    shared = set(truth) & set(predicted)
    matched = sum(
        1 for key in shared if _normalize_text(truth[key]) == _normalize_text(predicted[key])
    )
    return {
        "truth_cells": len(truth),
        "predicted_cells": len(predicted),
        "matched_cells": matched,
        "cell_precision": _ratio(matched, len(predicted)),
        "cell_recall": _ratio(matched, len(truth)),
        "cell_f1": _ratio(2 * matched, len(truth) + len(predicted)),
    }


def _table_page(table: Mapping[str, Any]) -> int | None:
    """The page a table record names, from a top-level ``page`` or its ``locator`` else ``None``."""

    page = table.get("page")
    if isinstance(page, int) and not isinstance(page, bool):
        return page
    locator = table.get("locator")
    if isinstance(locator, Mapping):
        page = cast("Mapping[str, Any]", locator).get("page")
        if isinstance(page, int) and not isinstance(page, bool):
            return page
    return None


def _cells(value: Any) -> dict[tuple[int, int], str]:
    """Normalize a table's cells to ``{(row, column): text}``.

    Accepts both the fixture truth mapping (``(row, column) -> text``) and the structure
    representation (a list of ``{"row", "col", "text"}`` records) so a scorer can compare them.
    Anything else normalizes to an empty map rather than raising.
    """

    if isinstance(value, Mapping):
        items: list[tuple[Any, Any]] = list(cast("Mapping[Any, Any]", value).items())
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = [
            ((record.get("row"), record.get("col")), record.get("text", ""))
            for record in (cast("Mapping[str, Any]", item) for item in cast("Sequence[Any]", value))
        ]
    else:
        return {}
    cells: dict[tuple[int, int], str] = {}
    for key, text in items:
        row, column = cast("tuple[Any, Any]", key)
        cells[(int(row), int(column))] = str(text)
    return cells


def _overlap_area(first: Sequence[float], second: Sequence[float]) -> float:
    """Intersection area of two ``[x0, y0, x1, y1]`` boxes; 0.0 when they do not overlap."""

    ax0, ay0, ax1, ay1 = first
    bx0, by0, bx1, by1 = second
    width = min(ax1, bx1) - max(ax0, bx0)
    height = min(ay1, by1) - max(ay0, by0)
    return width * height if width > 0 and height > 0 else 0.0


def match_tables(
    truth: Sequence[Mapping[str, Any]], predicted: Sequence[Mapping[str, Any]]
) -> list[tuple[int, int]]:
    """Greedy ``(truth_index, predicted_index)`` pairs, one predicted table per truth table.

    A pair matches only when both tables sit on the same page and their bounding boxes overlap; a
    candidate is claimed once, by the truth table it overlaps most, so two tables on one page cannot
    both claim the same detection. Deterministic because the outer loop follows the caller's truth
    order. Each table needs ``bbox`` and either a top-level ``page`` or a ``locator.page``.
    """

    used: set[int] = set()
    matches: list[tuple[int, int]] = []
    for truth_index, truth_table in enumerate(truth):
        page = _table_page(truth_table)
        bbox = cast("Sequence[float]", truth_table["bbox"])
        best_index = -1
        best_overlap = 0.0
        for index, prediction in enumerate(predicted):
            if index in used or _table_page(prediction) != page:
                continue
            overlap = _overlap_area(bbox, cast("Sequence[float]", prediction["bbox"]))
            if overlap > best_overlap:
                best_overlap = overlap
                best_index = index
        if best_index >= 0:
            used.add(best_index)
            matches.append((truth_index, best_index))
    return matches


def _preserves_locator(truth_table: Mapping[str, Any], prediction: Mapping[str, Any]) -> bool:
    """Whether a prediction's locator names the truth table's page and a positive table index."""

    locator = prediction.get("locator")
    if not isinstance(locator, Mapping):
        return False
    fields = cast("Mapping[str, Any]", locator)
    page = fields.get("page")
    index = fields.get("table_index")
    return (
        isinstance(page, int)
        and not isinstance(page, bool)
        and page == _table_page(truth_table)
        and isinstance(index, int)
        and not isinstance(index, bool)
        and index > 0
    )


def score_table_extraction(
    truth: Sequence[Mapping[str, Any]], predicted: Sequence[Mapping[str, Any]]
) -> dict[str, int | float | None]:
    """Detection, shape, cell, text and locator metrics over one fixture's table truth.

    :func:`match_tables` pairs each truth table with the prediction it overlaps most, then the
    numbers aggregate over those pairs: table detection precision/recall/F1 and false positives;
    row/column count accuracy; cell precision/recall/F1 (position-sensitive, via
    :func:`score_table_cells`); cell text accuracy (multiset of texts, position-insensitive); and
    locator/provenance preservation. Report only — TEST_STRATEGY.md §5 fixes no accuracy threshold
    in Phase 2.
    """

    matches = match_tables(truth, predicted)
    truth_cells_total = 0
    predicted_cells_total = 0
    matched_cells_total = 0
    text_hits = 0
    text_total = 0
    shape_correct = 0
    locators_preserved = 0
    for truth_index, predicted_index in matches:
        truth_table = truth[truth_index]
        prediction = predicted[predicted_index]
        truth_cells = _cells(truth_table["cells"])
        predicted_cells = _cells(prediction["cells"])
        cells = score_table_cells(truth_cells, predicted_cells)
        truth_cells_total += len(truth_cells)
        predicted_cells_total += len(predicted_cells)
        matched_cells_total += cast("int", cells["matched_cells"])
        truth_text = Counter(_normalize_text(text) for text in truth_cells.values())
        predicted_text = Counter(_normalize_text(text) for text in predicted_cells.values())
        text_hits += sum((truth_text & predicted_text).values())
        text_total += sum(truth_text.values())
        if truth_table["rows"] == prediction["rows"]:
            shape_correct += int(truth_table["columns"] == prediction["columns"])
        if _preserves_locator(truth_table, prediction):
            locators_preserved += 1
    matched = len(matches)
    return {
        "truth_tables": len(truth),
        "predicted_tables": len(predicted),
        "matched_tables": matched,
        "false_positives": len(predicted) - matched,
        "detection_precision": _ratio(matched, len(predicted)),
        "detection_recall": _ratio(matched, len(truth)),
        "detection_f1": _ratio(2 * matched, len(truth) + len(predicted)),
        "shape_correct_tables": shape_correct,
        "shape_accuracy": _ratio(shape_correct, matched),
        "truth_cells": truth_cells_total,
        "predicted_cells": predicted_cells_total,
        "matched_cells": matched_cells_total,
        "cell_precision": _ratio(matched_cells_total, predicted_cells_total),
        "cell_recall": _ratio(matched_cells_total, truth_cells_total),
        "cell_f1": _ratio(2 * matched_cells_total, truth_cells_total + predicted_cells_total),
        "cell_text_accuracy": _ratio(text_hits, text_total),
        "locators_preserved": locators_preserved,
        "locator_preservation": _ratio(locators_preserved, matched),
    }


def score_reading_order(
    truth: Sequence[str], predicted: Sequence[str]
) -> dict[str, int | float | bool | None]:
    """Agreement between a truth reading order and an extracted one.

    Two numbers, both deterministic and offline: ``exact_sequence_match`` (the whole sequence) and
    ``pair_order_agreement`` — the fraction of shared item pairs the two orders rank the same way,
    which is what M3's two-column check needs. Pair agreement is ``None`` when fewer than two items
    are shared, because one item fixes no order.
    """

    truth_items = [_normalize_text(item) for item in truth]
    predicted_items = [_normalize_text(item) for item in predicted]
    predicted_set = set(predicted_items)
    shared = [item for item in dict.fromkeys(truth_items) if item in predicted_set]
    truth_position = {item: index for index, item in enumerate(truth_items)}
    predicted_position = {item: index for index, item in enumerate(predicted_items)}
    pairs = 0
    agreed = 0
    for offset, first in enumerate(shared):
        for second in shared[offset + 1 :]:
            pairs += 1
            agreed += (truth_position[first] < truth_position[second]) == (
                predicted_position[first] < predicted_position[second]
            )
    return {
        "truth_items": len(truth_items),
        "predicted_items": len(predicted_items),
        "shared_items": len(shared),
        "exact_sequence_match": predicted_items == truth_items,
        "pair_order_agreement": _ratio(agreed, pairs),
    }


def score_furniture_detection(
    truth: Collection[str], predicted: Collection[str]
) -> dict[str, int | float | None]:
    """Precision/recall/F1 of detected page-furniture *keys* against the truth keys.

    M3 detects repeated headers/footers and page numbers inside the structure view; this scorer
    compares the set of detected keys with the set the fixture knows about, so a fixture with no
    furniture (``legitimate_repetition.pdf``) has an empty truth set and reports ``None`` precision
    — nothing was predicted and nothing should have been. ``false_positive_keys`` is reported
    separately because a false positive deletes legitimate body text from retrieval. Report only:
    TEST_STRATEGY.md §5 fixes no accuracy threshold in Phase 2.
    """

    truth_set = {_normalize_text(item) for item in truth}
    predicted_set = {_normalize_text(item) for item in predicted}
    matched = truth_set & predicted_set
    return {
        "truth_keys": len(truth_set),
        "predicted_keys": len(predicted_set),
        "matched_keys": len(matched),
        "false_positive_keys": len(predicted_set - truth_set),
        "precision": _ratio(len(matched), len(predicted_set)),
        "recall": _ratio(len(matched), len(truth_set)),
        "f1": _ratio(2 * len(matched), len(truth_set) + len(predicted_set)),
    }


def score_content_preservation(
    truth: Sequence[str], artifact_text: str
) -> dict[str, int | float | None]:
    """Share of truth lines that still appear in the artifact text (a no-silent-deletion check).

    M3 records reading order and furniture as a *view*, so every line the parser extracted must
    still reach the stored artifact even when a line is furniture. ``preservation`` is ``None`` for
    an empty truth, matching the Phase-2 metric convention.
    """

    normalized = _normalize_text(artifact_text)
    present = sum(1 for line in truth if _normalize_text(line) in normalized)
    return {
        "truth_lines": len(truth),
        "preserved_lines": present,
        "preservation": _ratio(present, len(truth)),
    }


__all__ = [
    "CONFIRMED",
    "PROBABLE",
    "UNKNOWN",
    "HarnessStatement",
    "citation_accuracy",
    "citation_presence",
    "label_coverage",
    "match_tables",
    "mean",
    "ndcg_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "score_content_preservation",
    "score_furniture_detection",
    "score_reading_order",
    "score_table_cells",
    "score_table_extraction",
    "unsupported_confirmed_rate",
]
