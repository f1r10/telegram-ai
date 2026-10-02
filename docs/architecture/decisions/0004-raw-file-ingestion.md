# ADR 0004: Raw File Staging and Ingestion Commit Boundary

## Status

Accepted and implemented in Phase 3.

## Decision

Use a narrow `FileStorage` protocol with `LocalFileStorage` as the V1 implementation. The application-controlled root contains generated paths under `staged/` and `blobs/`; user filenames remain metadata only. Original bytes are preserved exactly. A future S3-compatible adapter can implement the same boundary without changing application services.

Uploads from active private Telegram users are placed in one open PostgreSQL-backed ingestion session per user. Supported V1 types are PDF, DOCX, XLSX, TXT, JPG, JPEG, and PNG. The application calculates SHA-256 incrementally and reuses an existing physical blob for exact duplicate bytes while retaining every session item and Telegram provenance event.

`/saxla` is the explicit commit boundary. It activates staged file objects, marks the session `COMMITTED`, creates one `PENDING` PostgreSQL ingestion job, and writes audit events. No parser, LLM, embedding, or worker is invoked. Filesystem moves are compensated on transaction failure where possible; cleanup remains deterministic because filesystem and PostgreSQL do not share a distributed transaction.

Open sessions have a configurable TTL and are lazily expired. `/cancel` marks the session cancelled and deletes only unreferenced staged files. A cleanup command handles forgotten expired sessions. Redis, Celery, a queue service, cloud storage, and parser libraries are deliberately postponed.

## Consequences

- Raw uploads are durable and auditable before future processing exists.
- Exact duplicate detection is content-based, not filename-based.
- Operational upload limits protect the small local deployment without creating a product file-count limit.
- A later worker can claim `PENDING` jobs from PostgreSQL without changing the Telegram upload flow.
