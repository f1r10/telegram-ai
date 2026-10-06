# ADR 0008: Rebuildable Qdrant Knowledge Index

## Status

Accepted for Phase 5.

## Decision

PostgreSQL remains authoritative for ingestion, parse runs, and index-job state.
Qdrant is a rebuildable search projection. Successful normalized parses create
durable `knowledge_index_jobs` rows; the worker claims them with leases and
records each parse-run projection in `knowledge_index_records`.

The physical collection is versioned (`document_chunks_v1`) and exposed through
the logical alias `document_chunks`. Each point has named `dense` and `bm25`
vectors. Hybrid retrieval uses Qdrant reciprocal-rank fusion (RRF), with
metadata filters for file ID, parse-run ID, and media type.

Chunk IDs are deterministic hashes of source file, parse run, chunking version,
sequence, and locator. Re-running an index job upserts the same points instead
of duplicating them. A future rebuild can create a new physical version,
validate it, and move the alias without changing callers.

## Consequences

- Qdrant loss does not make PostgreSQL data unavailable; the projection can be rebuilt.
- The local Compose volume persists Qdrant data, but it is not the system of record.
- No answer generation, reranking, OCR, web search, or business extraction is part of this phase.
