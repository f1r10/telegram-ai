# ADR 0009: Versioned Chunks and Local Embeddings

## Status

Accepted for Phase 5.

## Decision

Normalized parser artifacts are chunked deterministically with the versioned
configuration `v1-medium-1800-180`: a 1,800-character maximum and 180-character
overlap for oversized blocks. Every chunk preserves file ID, parse-run ID,
sequence, locator, source text, and block type metadata.

Dense embeddings run locally through FastEmbed. The default candidate is
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dimensions,
Apache-2.0). The benchmark also evaluates
`minishlab/potion-multilingual-128M` (256 dimensions, MIT). FastEmbed does not
directly register `intfloat/multilingual-e5-small` in the installed catalog;
it is not silently substituted or custom-integrated in Phase 5.

Sparse retrieval uses `Qdrant/bm25` through the local adapter. Identifier aliases
are added conservatively; no English-only stemming or stop-word removal is
applied to mixed-language company text. Model cache files live on the persistent
Compose `model_cache` volume and indexing uses bounded batches.

## Consequences

- The application has no OpenAI dependency for indexing or query vectors.
- Embedding model and chunking versions are stored with index metadata.
- Quality is measured with synthetic Azerbaijani, Turkish, Russian, and English queries.
- OCR, image embeddings, reranking, and LLM answer synthesis remain outside Phase 5.
