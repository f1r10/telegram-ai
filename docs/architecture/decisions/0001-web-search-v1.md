# ADR 0001: Cost-Optimized Web Search for V1

## Status

Accepted. Implementation is deferred until the web-search phase.

## Decision

Use a `WebSearchProvider` abstraction. A private self-hosted SearXNG service will be the primary provider, with Tavily as a controlled fallback. SearXNG will run inside the Docker Compose network and will not be publicly exposed by default.

Search results remain external evidence and must never silently become authoritative company data. `/internet` uses external search; `/tamaxtar` distinguishes PostgreSQL/Qdrant evidence from web evidence. Provider failures, fallback reasons, latency, result counts, and cache behavior must be observable and testable.

PostgreSQL will provide the V1 search cache. Redis is not introduced only for caching. Web pages will be fetched separately and only when needed; search results and page content are untrusted data and cannot grant permissions or override system instructions.

## Consequences

- Normal search traffic can operate without per-query API billing.
- SearXNG reliability and upstream blocking must be benchmarked before production use.
- Tavily credentials remain optional and are used only under explicit fallback conditions.
- The provider interface can later support Exa, Brave, or another service without changing business logic.

