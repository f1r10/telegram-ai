# ADR 0011: Authoritative Structured Business Domain

## Status

Accepted for Phase 7.

## Decision

PostgreSQL is authoritative for products, customers, aliases, historical prices,
payments, business events, proposals, proposal documents, and proposal items.
Qdrant remains a rebuildable unstructured-document search projection. OpenAI may
later interpret requests and synthesize answers, but it is not authoritative.

Business entities use UUID identifiers. Product codes use Unicode NFKC, case
normalization, and removal of controlled code separators (`space`, `-`, `_`).
Customer names use NFKC, casefolding, whitespace collapse, and conservative
punctuation handling; legal suffixes and Azerbaijani letters are preserved.
Globally unique normalized aliases prevent ambiguous ownership. Cross-table
identity checks are serialized during writes and database unique constraints
remain the final concurrency boundary.

Prices are immutable `NUMERIC(18,4)` history rows, not a mutable product field.
General and customer-specific scopes are distinct. Resolution selects the unique
latest applicable, non-superseded effective timestamp. No result produces
`NOT_FOUND`; multiple latest rows produce `CONFLICT`. Supersession preserves the
old row for history while removing it from resolution. There is no implicit
source precedence or customer-to-general fallback.

Important business facts reference a reusable `source_references` row. It stores
bounded origin pointers and locators, never complete document text. Mutations,
provenance, and audit records share one application-owned transaction.

## Consequences

- Structured queries work without OpenAI or Qdrant.
- Multi-currency payment totals are grouped by currency and never combined.
- Unresolved proposal lines retain their raw product label with a null product ID.
- ACTIVE users may create facts; only ADMIN may deactivate entities or supersede prices.
- Physical deletion is outside ordinary application services.
- AI extraction, entity matching, OCR, web search, and autonomous routing remain deferred.
