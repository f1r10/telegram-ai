# ADR 0010: Grounded OpenAI Responses and Application-Owned Conversations

## Status

Accepted for Phase 6.

## Decision

Phase 6 uses the official OpenAI Python SDK with the Responses API behind the
`LanguageModel` protocol. The default configured model is `gpt-6-luna`; the
strong model setting is `gpt-6.1-sol` for future explicitly justified use and is
not automatically selected. Model identifiers are configuration, not literals
spread through handlers.

`/baza` retrieves a small hybrid Qdrant result set, builds deterministic source
IDs (`S1`, `S2`, ...), applies a context-token budget, and requests structured
output from the model. The provider receives source blocks as untrusted data.
The application validates returned citation IDs and converts them to numbered
Telegram citations using parser provenance. No evidence means no provider call.

General chat uses the same default model without internal sources and explicitly
distinguishes world knowledge from company facts. Ordinary text remains an
ingestion instruction whenever the user has an open `/saxla` session.

Conversations and usage telemetry are application-owned in PostgreSQL. Provider
conversation persistence is disabled with `store=false`; OpenAI response IDs
are telemetry, not primary keys. History is bounded by message and token limits.

## Consequences

- Handlers remain thin and do not depend on SDK response objects.
- Qdrant failure cannot fall back to model memory for `/baza`.
- Citation and structured-output validation can reject unsafe or malformed results.
- The current Phase 6 does not use OpenAI hosted File Search, web search, tools, or agent frameworks.
