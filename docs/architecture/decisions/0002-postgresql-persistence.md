# ADR 0002: PostgreSQL Persistence Foundation

## Status

Accepted and implemented in Phase 1.

## Decision

Use PostgreSQL as the authoritative store for future structured company data. Use SQLAlchemy 2.x with `AsyncEngine` and `AsyncSession` for asynchronous access, `asyncpg` as the PostgreSQL driver, and Alembic for migration-driven schema evolution.

The initial connection pool is intentionally modest for approximately 3-4 active users. Sessions are short-lived and transaction boundaries are explicit; repository calls must not silently commit. Timestamps use timezone-aware UTC-compatible database values. The first migration creates only `system_metadata`, a small technical table used to prove persistence without guessing the future business schema.

SQLite is not used for PostgreSQL integration tests because its types, constraints, transactions, indexing, and SQL behavior differ materially. Qdrant is not introduced here because it belongs to the future retrieval layer and has a different responsibility from PostgreSQL.

## Consequences

- Local development requires PostgreSQL through Docker Compose for persistence integration tests.
- Schema changes are explicit and reviewable through Alembic migrations.
- Future domain repositories can be added without introducing a generic repository framework.
- Production database credentials remain environment/secrets configuration and are never stored in `alembic.ini`.
