"""Domain-neutral dense, sparse, and hybrid retrieval service."""

from collections.abc import Sequence

from office_assistant.knowledge.embeddings import FastEmbedSparseAdapter, FastEmbedTextAdapter
from office_assistant.knowledge.identifiers import normalize_query
from office_assistant.knowledge.models import RetrievalFilter, RetrievedChunk
from office_assistant.knowledge.qdrant_store import QdrantKnowledgeStore


class KnowledgeRetrievalService:
    def __init__(
        self,
        store: QdrantKnowledgeStore,
        embedder: FastEmbedTextAdapter,
        sparse_embedder: FastEmbedSparseAdapter,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.sparse_embedder = sparse_embedder

    async def search_internal_knowledge(
        self,
        query: str,
        *,
        mode: str = "hybrid",
        filters: RetrievalFilter | None = None,
        limit: int = 5,
    ) -> list[RetrievedChunk]:
        if not query.strip() or limit <= 0:
            return []
        normalized = normalize_query(query)
        if mode == "dense":
            vector = (await _embed(self.embedder.embed_queries, [normalized]))[0]
            return await self.store.search_dense(vector, limit=limit, filters=filters)
        sparse = (await _sparse(self.sparse_embedder.embed, [normalized]))[0]
        if mode == "bm25":
            return await self.store.search_sparse(sparse, limit=limit, filters=filters)
        if mode != "hybrid":
            raise ValueError(f"Unsupported retrieval mode: {mode}")
        vector = (await _embed(self.embedder.embed_queries, [normalized]))[0]
        return await self.store.search_hybrid(vector, sparse, limit=limit, filters=filters)


async def _embed(function: object, texts: Sequence[str]) -> list[list[float]]:
    import asyncio

    return await asyncio.to_thread(function, texts)  # type: ignore[call-arg, no-any-return]


async def _sparse(function: object, texts: Sequence[str]) -> list[tuple[list[int], list[float]]]:
    import asyncio

    return await asyncio.to_thread(function, texts)  # type: ignore[call-arg, no-any-return]
