from office_assistant.knowledge.benchmark import (
    BenchmarkQuery,
    reciprocal_rank_fusion,
    retrieval_metrics,
)


def test_rrf_and_metrics_are_deterministic() -> None:
    assert reciprocal_rank_fusion([["a", "b"], ["b", "a"]]) == ["a", "b"]
    query = BenchmarkQuery("q", "b", "test")
    metrics = retrieval_metrics([query], {"q": ["a", "b"]})
    assert metrics == {"recall@1": 0.0, "recall@3": 1.0, "recall@5": 1.0, "mrr": 0.5}
