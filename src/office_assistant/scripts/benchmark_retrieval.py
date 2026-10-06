"""Benchmark local dense, BM25, and RRF retrieval without company data."""

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

from office_assistant.core.settings import Settings, load_settings
from office_assistant.knowledge.benchmark import (
    BenchmarkDocument,
    BenchmarkQuery,
    reciprocal_rank_fusion,
    retrieval_metrics,
)
from office_assistant.knowledge.embeddings import FastEmbedSparseAdapter, FastEmbedTextAdapter

DOCUMENTS = [
    BenchmarkDocument(
        "KA45",
        "KA45 məhsulunun zəmanət müddəti 24 aydır. Servis müraciəti üçün alış sənədi tələb olunur.",
        "identifier",
    ),
    BenchmarkDocument(
        "KA46",
        "KA46 məhsulunun zəmanət müddəti 12 aydır və servis qaydaları ayrıca göstərilib.",
        "identifier",
    ),
    BenchmarkDocument(
        "PR-2026-017",
        (
            "PR-2026-017 layihə təklifi Azərbaycan və ingilis dillərində daxili "
            "təsdiq mərhələlərini izah edir."
        ),
        "identifier",
    ),
    BenchmarkDocument(
        "ABC MMC",
        "ABC MMC üçün müştəri müqaviləsində ödəniş qrafiki və xidmət şərtləri qeyd edilir.",
        "entity",
    ),
    BenchmarkDocument(
        "RU-TECH",
        "Технический документ описывает резервное копирование и восстановление данных.",
        "language",
    ),
    BenchmarkDocument(
        "TR-SERVIS",
        "Türkçe servis belgesi garanti süresi ve bakım koşullarını açıklar.",
        "language",
    ),
]

QUERIES = [
    BenchmarkQuery("KA45", "KA45", "identifier"),
    BenchmarkQuery("KA-45 zəmanəti neçə aydır?", "KA45", "identifier"),
    BenchmarkQuery("PR 2026 017 təsdiq", "PR-2026-017", "identifier"),
    BenchmarkQuery("ABC MMC ödəniş qrafiki", "ABC MMC", "entity"),
    BenchmarkQuery("резервное копирование", "RU-TECH", "language"),
    BenchmarkQuery("garanti süresi bakım", "TR-SERVIS", "language"),
]


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _sparse_score(
    left: tuple[list[int], list[float]], right: tuple[list[int], list[float]]
) -> float:
    values = dict(zip(right[0], right[1], strict=True))
    return sum(
        value * values.get(index, 0.0) for index, value in zip(left[0], left[1], strict=True)
    )


def _rank(ids: list[str], scores: list[float]) -> list[str]:
    return [
        item
        for item, _ in sorted(zip(ids, scores, strict=True), key=lambda pair: (-pair[1], pair[0]))
    ]


def _run_model(model_id: str, cache_dir: Path, batch_size: int) -> dict[str, Any]:
    started = time.perf_counter()
    dense = FastEmbedTextAdapter(model_id, cache_dir, batch_size=batch_size)
    sparse = FastEmbedSparseAdapter(cache_dir=cache_dir)
    document_texts = [document.text for document in DOCUMENTS]
    document_ids = [document.document_id for document in DOCUMENTS]
    query_texts = [query.query for query in QUERIES]
    doc_dense = dense.embed_documents(document_texts)
    query_dense = dense.embed_queries(query_texts)
    doc_sparse = sparse.embed(document_texts)
    query_sparse = sparse.embed(query_texts)
    dense_rankings: dict[str, list[str]] = {}
    sparse_rankings: dict[str, list[str]] = {}
    hybrid_rankings: dict[str, list[str]] = {}
    for query, dense_vector, sparse_vector in zip(QUERIES, query_dense, query_sparse, strict=True):
        dense_rank = _rank(document_ids, [_cosine(dense_vector, item) for item in doc_dense])
        sparse_rank = _rank(
            document_ids, [_sparse_score(sparse_vector, item) for item in doc_sparse]
        )
        dense_rankings[query.query] = dense_rank
        sparse_rankings[query.query] = sparse_rank
        hybrid_rankings[query.query] = reciprocal_rank_fusion([dense_rank, sparse_rank])
    elapsed = time.perf_counter() - started
    by_category: dict[str, dict[str, Any]] = {}
    for category in sorted({query.category for query in QUERIES}):
        category_queries = [query for query in QUERIES if query.category == category]
        by_category[category] = {
            "dense": retrieval_metrics(category_queries, dense_rankings),
            "bm25": retrieval_metrics(category_queries, sparse_rankings),
            "hybrid_rrf": retrieval_metrics(category_queries, hybrid_rankings),
        }
    return {
        "model": model_id,
        "dimension": dense.dimension,
        "documents": len(DOCUMENTS),
        "queries": len(QUERIES),
        "elapsed_seconds": round(elapsed, 3),
        "dense": retrieval_metrics(QUERIES, dense_rankings),
        "bm25": retrieval_metrics(QUERIES, sparse_rankings),
        "hybrid_rrf": retrieval_metrics(QUERIES, hybrid_rankings),
        "by_category": by_category,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("var/benchmark/retrieval.json"))
    args = parser.parse_args()
    settings: Settings = load_settings()
    model_ids = [
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "minishlab/potion-multilingual-128M",
    ]
    results = [
        _run_model(model, Path(settings.embedding_cache_path), settings.embedding_batch_size)
        for model in model_ids
    ]
    payload = {"corpus": "synthetic", "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
