# ADR 0014: Bounded internal evidence routing

## Status

Accepted for Phase 10.

## Context

Internal questions can require authoritative PostgreSQL business state, document
wording from the Qdrant projection, or both. Treating these channels as an
exclusive choice hides useful provenance and can conceal disagreements. Users
also need to request an original cited file without exposing storage identifiers
or allowing one Telegram user to resolve another user's source context.

## Decision

Application code owns routing and orchestration. Deterministic fast paths are used
first. An unresolved question may use one structured planning call. The planner
can select only the 13 read-only Phase 9 tools through a provider adapter and no
turn may execute more than four business-tool calls. Actor context is constructed
from the authenticated application user. Qdrant remains behind the narrow
`InternalDocumentSearch` abstraction. There is no autonomous agent loop.

Collected results form a typed `EvidenceBundle`. Structured authoritative state,
document evidence, statuses, warnings, provenance, and conflicts remain distinct.
The application assigns evidence IDs and validates all model citations. Internal
synthesis sees only collected evidence, treats every source as untrusted data,
and falls back to a deterministic evidence summary on provider failure. Empty
evidence returns a deterministic no-source response without synthesis.

Recent cited sources are stored as bounded metadata in `ai_message_sources`.
Mappings are scoped indirectly through the user's internal conversation, expire
after a configured TTL, and retain only a configured recent-message window. File
follow-ups resolve citation number to PostgreSQL `file_id`, then to an ACTIVE
`file_objects` row, and finally to the original committed blob under the storage
`blobs` directory. Telegram receives a sanitized original filename. Derived
artifacts, arbitrary paths, fuzzy filenames, and raw storage keys are excluded.

Phase 10 document retrieval applies an acceptance policy after Qdrant hybrid RRF
ranking. Live synthetic-corpus evaluation produced a relevant KA45 score of
`0.833333` and unrelated tail scores of `0.5` and below. The initial configurable
general floor is therefore `0.55`. Queries containing a strong identifier use an
exact normalized-identifier gate (`KA45`, `KA-45`, and `KA 45` are equivalent)
plus a lower `0.30` score floor so exact lower-ranked evidence is not discarded.
Codes are never edit-distance matched, so KA45 cannot become KA46. A result limit
is an upper bound; zero or fewer-than-limit accepted results are valid.

Only model-cited evidence IDs and application-required conflict records survive
final citation pruning. Logical duplicates use source class, underlying source
type, file ID, locator, and business-record identity as a stable key. Distinct
conflicting price records remain separate even when their file provenance is the
same. Final citations are renumbered contiguously before `ai_message_sources` is
persisted, so source-number delivery resolves exactly what the user saw.

Proposal comparison treats the first proposal as `before`, the second as `after`,
and publishes `delta_direction=after_minus_before`. The application passes older
then newer proposals. Latest-proposal questions that request contents, quantities,
prices, totals, or products deterministically add `get_proposal_details` after the
summary lookup without another planner call. A small plan normalizer removes
`find_product` when `resolve_product_price` already provides the required lookup.

Conversational query normalization is deliberately separate from authoritative
entity normalization. Routing accepts narrow Azerbaijani ASCII and colloquial
variants, source ordinals, and contextual possessive suffixes. Original text is
retained for history and model context. Product/customer resolution still uses
the strict Phase 7/9 normalizers, with no fuzzy correction of identifiers.

## Consequences

- PostgreSQL and Qdrant evidence can appear together and disagreements remain
  visible.
- General chat and deterministic source-file follow-ups avoid planner calls.
- No-source company questions cannot fall back to pretrained model knowledge.
- Recent source delivery is user-scoped and time/window bounded.
- Weak retrieval tails and uncited evidence do not become user-visible sources.
- Informal Azerbaijani improves intent recognition without weakening entity identity.
- The architecture adds one migration and an application service, but no agent
  framework, web provider, OCR, image embedding, mutation tool, or arbitrary SQL.
