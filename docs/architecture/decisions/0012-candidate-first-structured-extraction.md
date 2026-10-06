# ADR 0012: Candidate-First Structured Extraction

## Status

Accepted for Phase 8.

## Decision

Saved sessions are parsed and indexed before structured extraction. The extractor
reads ordered normalized artifacts, builds bounded batches, and calls
`gpt-6-luna` through the existing structured Responses API adapter. There is no
automatic strong-model escalation.

Model output is untrusted and persists only as allowlisted typed candidates.
Application code validates source IDs, values, dates, currency, identity,
duplicates, and conflicts. Confidence is metadata, never authorization. The
model receives no database mutation tools.

Candidates require owner or ADMIN review. Safe facts go through Phase 7 services
with the approving human as actor. Conflicts remain unapplied. Proposal headers
and items form one atomic group; independent facts apply separately. Telegram
shows compact counts and paginated problems, never every large-sheet row.

Original files, normalized artifacts, and Qdrant knowledge remain valid when
extraction fails or is rejected. OCR, vision, web search, agents, and global
source precedence remain deferred.
