# ADR 0005: PostgreSQL Worker Queue

## Decision

Use PostgreSQL `FOR UPDATE SKIP LOCKED` as the V1 ingestion queue. A dedicated worker claims one `PENDING` job in a short transaction, increments its attempt count, and records a lease. Expired `RUNNING` leases are reclaimable by another worker. Attempts are bounded by `WORKER_MAX_ATTEMPTS` (default 3); transient worker failures return a job to `PENDING` with a small backoff, while deterministic per-file parser failures are recorded without crashing the worker.

The worker is a separate Compose service and shares the persistent storage volume with the Telegram application. Completion writes a generic PostgreSQL outbox event in the same transaction as the terminal job state; the Telegram process consumes the event and sends a compact Azerbaijani notification.

## Rationale and replacement path

For three to four internal users, polling PostgreSQL is small, inspectable, and avoids Redis, Celery, RabbitMQ, and operational infrastructure. A future queue can replace `claim_next` and the outbox consumer behind the application service boundary without changing parser or business logic.
