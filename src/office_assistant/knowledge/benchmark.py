"""Deterministic, local-only retrieval benchmark helpers."""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BenchmarkDocument:
    document_id: str
    text: str
    category: str


@dataclass(frozen=True, slots=True)
class BenchmarkQuery:
    query: str
    expected_document_id: str
    category: str


def reciprocal_rank_fusion(ranked_lists: Iterable[list[str]], *, k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return [item for item, _ in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))]


def retrieval_metrics(
    queries: Iterable[BenchmarkQuery], rankings: dict[str, list[str]]
) -> dict[str, float]:
    rows = list(queries)
    if not rows:
        return {"recall@1": 0.0, "recall@3": 0.0, "recall@5": 0.0, "mrr": 0.0}
    recalls = {1: 0, 3: 0, 5: 0}
    reciprocal_sum = 0.0
    for item in rows:
        ranked = rankings[item.query]
        try:
            rank = ranked.index(item.expected_document_id) + 1
        except ValueError:
            rank = 0
        for cutoff in recalls:
            recalls[cutoff] += int(0 < rank <= cutoff)
        reciprocal_sum += 1.0 / rank if rank else 0.0
    total = len(rows)
    return {
        "recall@1": recalls[1] / total,
        "recall@3": recalls[3] / total,
        "recall@5": recalls[5] / total,
        "mrr": reciprocal_sum / total,
    }
