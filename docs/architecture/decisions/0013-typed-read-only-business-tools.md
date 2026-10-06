# ADR 0013: Typed Read-Only Business Tools

## Status

Accepted for Phase 9.

## Decision

Authoritative PostgreSQL business reads are exposed through an explicit,
provider-independent `BusinessToolRegistry`. Every tool has strict Pydantic input
and output models, receives application-owned authenticated actor context, and
returns explicit `OK`, `NOT_FOUND`, `AMBIGUOUS`, `CONFLICT`, or
`INVALID_REQUEST` state.

The registry is allowlisted and read-only. It exposes no SQL, generic query
language, repository reflection, or mutation method. Identity resolution and
proposal comparison are deterministic; monetary calculations use `Decimal` and
different currencies are never subtracted. Results are bounded and contain
display-neutral provenance rather than raw ORM objects or source content.

PostgreSQL is authoritative for structured facts. Qdrant remains document/RAG
retrieval and is not a fallback inside these tools. OpenAI adapters may consume
exported schemas later, but autonomous routing and tool calling are postponed to
Phase 10.
