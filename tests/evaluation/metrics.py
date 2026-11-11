"""Metric primitives for the S17.3 evaluation table.

All functions are pure and offline. ``docs/evaluation/BENCHMARK_RESULTS.md`` is generated from these
numbers, so each metric documents its degenerate case explicitly rather than guessing.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

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


__all__ = [
    "CONFIRMED",
    "PROBABLE",
    "UNKNOWN",
    "HarnessStatement",
    "citation_accuracy",
    "citation_presence",
    "label_coverage",
    "mean",
    "ndcg_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "score_reading_order",
    "score_table_cells",
    "unsupported_confirmed_rate",
]
