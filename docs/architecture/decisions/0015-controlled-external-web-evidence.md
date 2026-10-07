# ADR 0015: Controlled external web evidence

## Status

Accepted for Phase 11. Amended 2026-10-07 (Block 4) to record how the approved
"no usable result" trigger, the freshness window and the degraded combined answer
are implemented; the decision itself is unchanged.

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

"No usable result" is measured after the relevance gate, not at the raw hit count:
the answer layer passes its own usability test into the search layer, so a page of
hits that the gate rejects reaches the fallback exactly as an empty page does. The
cache read uses the same test, so a stored page with nothing usable is re-searched
instead of served. Usability stays the caller's decision because it depends on the
question; the search layer never redefines it. Each engagement logs one reason
(`no_usable_results`, or the provider error type) with hit counts and never the
query or answer text.

Before fetching pages or synthesizing answers, identifier-bearing queries require
an exact normalized identifier match in each result's title or snippet. This gate
also applies to cached results. It conservatively drops hits with no visible
identifier; matching an identifier alone does not establish manufacturer identity.
The gate further requires that a hit carrying its own publication date fall inside
the requested time window, because a dated hit outside the window was never current
information; an undated hit stays, since absence of a date is not proof of age.
Without accepted web evidence, combined answers retain the internal answer and
citations and report that suitable public evidence was not found. When the search
providers themselves fail, a combined answer still delivers the internal half and
states that the internet part was unavailable, rather than discarding work already
done; an external-only answer reports the outage. Which provider answered is
reported even when the answer came from the cache.

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
