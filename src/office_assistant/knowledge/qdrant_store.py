"""Narrow Qdrant adapter for versioned hybrid retrieval."""

# Qdrant's SDK models are generated dynamically; application code consumes
# only the typed RetrievedChunk boundary below.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from collections.abc import Sequence
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from office_assistant.knowledge.models import DocumentChunk, RetrievalFilter, RetrievedChunk


class QdrantUnavailable(RuntimeError):
    """Raised when the retrieval projection cannot be reached."""


class QdrantKnowledgeStore:
    def __init__(self, url: str, *, alias: str, collection: str, timeout: int = 10) -> None:
        self.client = AsyncQdrantClient(url=url, timeout=timeout)
        self.alias = alias
        self.collection = collection
        self.timeout = timeout

    async def close(self) -> None:
        await self.client.close()

    async def health_check(self) -> bool:
        try:
            await self.client.get_collections()
        except Exception:
            return False
        return True

    async def resolve_target(self) -> str:
        """Return the live projection name reads must use, or fail loudly.

        The worker creates the alias during its first indexing run, so a fresh
        projection can hold points only under its physical collection name.
        """
        target = await self._target()
        if target is None:
            raise QdrantUnavailable(
                f"Retrieval projection is missing | alias={self.alias} collection={self.collection}"
            )
        return target

    async def _target(self) -> str | None:
        for name in (self.alias, self.collection):
            try:
                await self.client.get_collection(name)
            except Exception as error:
                if getattr(error, "status_code", None) == 404:
                    continue
                raise QdrantUnavailable(
                    f"Qdrant is unreachable | error_type={type(error).__name__}"
                ) from error
            return name
        return None

    async def _points(self, **kwargs: Any) -> Sequence[Any]:
        target = await self.resolve_target()
        try:
            response = await self.client.query_points(target, timeout=self.timeout, **kwargs)
        except Exception as error:
            raise QdrantUnavailable(
                f"Qdrant read failed | error_type={type(error).__name__}"
            ) from error
        return response.points

    async def ensure_collection(self, dimension: int) -> None:
        try:
            await self.client.get_collection(self.collection)
        except Exception:
            await self.client.create_collection(
                collection_name=self.collection,
                vectors_config={
                    "dense": models.VectorParams(size=dimension, distance=models.Distance.COSINE)
                },
                sparse_vectors_config={
                    "bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)
                },
                on_disk_payload=True,
            )
            for field in ("file_id", "parse_run_id", "media_type"):
                await self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD
                    if field == "media_type"
                    else models.PayloadSchemaType.INTEGER,
                )
        aliases = await self.client.get_aliases()
        if not any(alias.alias_name == self.alias for alias in aliases.aliases):
            await self.client.update_collection_aliases(
                [
                    models.CreateAliasOperation(
                        create_alias=models.CreateAlias(
                            collection_name=self.collection, alias_name=self.alias
                        )
                    )
                ]
            )

    async def upsert_chunks(
        self,
        chunks: Sequence[DocumentChunk],
        dense_vectors: Sequence[list[float]],
        sparse_vectors: Sequence[tuple[list[int], list[float]]],
        *,
        source_filename: str,
        media_type: str,
        embedding_model: str,
    ) -> None:
        points = []
        for chunk, dense, sparse in zip(chunks, dense_vectors, sparse_vectors, strict=True):
            points.append(
                models.PointStruct(
                    id=chunk.chunk_id,
                    vector={
                        "dense": dense,
                        "bm25": models.SparseVector(indices=sparse[0], values=sparse[1]),
                    },
                    payload={
                        "chunk_id": chunk.chunk_id,
                        "file_id": chunk.source_file_id,
                        "parse_run_id": chunk.parse_run_id,
                        "source_filename": source_filename,
                        "media_type": media_type,
                        "locator": chunk.locator,
                        "chunk_sequence": chunk.sequence,
                        "chunking_version": chunk.chunking_version,
                        "embedding_model": embedding_model,
                        "text": chunk.text,
                        "source_text": chunk.source_text,
                        "block_types": list(chunk.block_types),
                    },
                )
            )
        if points:
            await self.client.upsert(
                self.collection, points=points, wait=True, timeout=self.timeout
            )

    async def delete_file(self, file_id: int) -> None:
        target = await self._target()
        if target is None:
            # Nothing is indexed, so there are no points left to remove.
            return
        try:
            await self.client.delete(
                target,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="file_id", match=models.MatchValue(value=file_id)
                            )
                        ]
                    )
                ),
                wait=True,
                timeout=self.timeout,
            )
        except Exception as error:
            raise QdrantUnavailable(
                f"Qdrant delete failed | error_type={type(error).__name__}"
            ) from error

    async def search_dense(
        self, vector: list[float], *, limit: int, filters: RetrievalFilter | None = None
    ) -> list[RetrievedChunk]:
        points = await self._points(
            query=vector,
            using="dense",
            query_filter=self._filter(filters),
            limit=limit,
            with_payload=True,
        )
        return self._results(points, "dense")

    async def search_sparse(
        self,
        vector: tuple[list[int], list[float]],
        *,
        limit: int,
        filters: RetrievalFilter | None = None,
    ) -> list[RetrievedChunk]:
        points = await self._points(
            query=models.SparseVector(indices=vector[0], values=vector[1]),
            using="bm25",
            query_filter=self._filter(filters),
            limit=limit,
            with_payload=True,
        )
        return self._results(points, "bm25")

    async def search_hybrid(
        self,
        dense: list[float],
        sparse: tuple[list[int], list[float]],
        *,
        limit: int,
        filters: RetrievalFilter | None = None,
    ) -> list[RetrievedChunk]:
        points = await self._points(
            prefetch=[
                models.Prefetch(query=dense, using="dense", limit=max(limit * 3, 10)),
                models.Prefetch(
                    query=models.SparseVector(indices=sparse[0], values=sparse[1]),
                    using="bm25",
                    limit=max(limit * 3, 10),
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=self._filter(filters),
            limit=limit,
            with_payload=True,
        )
        return self._results(points, "hybrid")

    @staticmethod
    def _filter(filters: RetrievalFilter | None) -> models.Filter | None:
        if filters is None:
            return None
        must = []
        if filters.file_id is not None:
            must.append(
                models.FieldCondition(key="file_id", match=models.MatchValue(value=filters.file_id))
            )
        if filters.parse_run_id is not None:
            must.append(
                models.FieldCondition(
                    key="parse_run_id", match=models.MatchValue(value=filters.parse_run_id)
                )
            )
        if filters.media_type is not None:
            must.append(
                models.FieldCondition(
                    key="media_type", match=models.MatchValue(value=filters.media_type)
                )
            )
        return models.Filter(must=must) if must else None

    @staticmethod
    def _results(points: Sequence[Any], method: str) -> list[RetrievedChunk]:
        results = []
        for rank, point in enumerate(points, start=1):
            payload = dict(point.payload or {})
            results.append(
                RetrievedChunk(
                    chunk_id=str(point.id).replace("-", ""),
                    text=str(payload.get("text", "")),
                    source_filename=str(payload.get("source_filename", "")),
                    source_file_id=int(payload.get("file_id", 0)),
                    parse_run_id=int(payload.get("parse_run_id", 0)),
                    locator=payload.get("locator", {}),
                    retrieval_method=method,
                    score=float(point.score),
                    rank=rank,
                    payload=payload,
                )
            )
        return results
