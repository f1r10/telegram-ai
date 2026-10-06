# ADR 0015: Controlled external web evidence

## Status

Accepted for Phase 11.

## Decision

Use a provider-neutral `WebSearchProvider` boundary with private SearXNG as the
primary provider and optional Tavily as fallback. Providers are called serially:
cache, SearXNG, then Tavily only on timeout/unavailability/malformed output/no
usable result. There is no default fan-out.

An unset, empty, or whitespace-only `TAVILY_API_KEY` disables the fallback.
The Tavily adapter also rejects missing credentials before sending HTTP requests.
Each fallback is attempted at most once, including when the primary returns no
results. With no configured fallback, an empty primary result is reported as no
public evidence; it must not produce an invalid empty Bearer header.

Before fetching pages or synthesizing answers, identifier-bearing queries require
an exact normalized identifier match in each result's title or snippet. This gate
also applies to cached results. It conservatively drops hits with no visible
identifier; matching an identifier alone does not establish manufacturer identity.
Without accepted web evidence, combined answers retain the internal answer and
citations and report that suitable public evidence was not found.

User-facing answers use Azerbaijani source names rather than internal class labels.
Mixed Latin/Cyrillic word repair is limited to known letter substitutions and
preserves wholly Cyrillic names, URLs, and inline code.

Store provider-neutral search results in PostgreSQL `web_search_cache`. Cache keys
include the normalized query and all supported semantic parameters. Use a longer
TTL for stable queries and a shorter TTL for explicit freshness/current/price
intent. Final AI prose is not cached.

Keep search and page retrieval separate. `WebContentFetcher` validates public
HTTP/HTTPS destinations and every redirect, rejects special/private/internal
addresses, limits content type/bytes/time/redirects, and extracts readable text
with Trafilatura before synthesis. Browser rendering is outside Phase 11.

`/internet` is external-only. `/tamaxtar` compares internal and external evidence.
`/baza` remains internal-only. `EXTERNAL_WEB` is non-authoritative and cannot
mutate or supersede PostgreSQL business facts or internal documents. Application
code owns citation IDs and URLs; model-produced URLs are never trusted.

## Consequences

The app remains usable for internal work when SearXNG is down. Tavily is optional
and may incur external SaaS cost only after an explicit fallback condition.
SearXNG is a separately deployed AGPL service, while Trafilatura 2.3.0 is
Apache-2.0. DNS validation materially reduces SSRF risk, though production network
egress policy remains recommended defense in depth against sophisticated DNS
rebinding.
