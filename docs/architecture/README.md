# Architecture Documentation

This directory contains repository-local architecture notes and approved decision records.

The original research report is currently outside this repository at:

`C:\Users\User\Downloads\deep-research-report.md`

That report is reference material. Repository decisions should be recorded here so they are versioned with the code.

## Current status

- Phases 0-11 are implemented, including authoritative structured business data,
  candidate-first human-confirmed extraction, typed read-only business tools, and
  bounded multi-source internal evidence orchestration.
- PostgreSQL remains authoritative; Qdrant, local dense embeddings, BM25, deterministic chunking, and hybrid retrieval are implemented behind narrow adapters.
- OpenAI Responses API answer generation is implemented behind an application-owned provider abstraction; internal answers may combine PostgreSQL and Qdrant evidence while general chat remains separate.
- The approved web-search decision is recorded in `decisions/0001-web-search-v1.md`.
- The persistence decision is recorded in `decisions/0002-postgresql-persistence.md`.
- The Telegram authentication decision is recorded in `decisions/0003-telegram-authentication.md`.
- The raw ingestion decision is recorded in `decisions/0004-raw-file-ingestion.md`.
- The PostgreSQL worker decision is recorded in `decisions/0005-postgresql-worker-queue.md`.
- The parser architecture decision is recorded in `decisions/0006-deterministic-document-parsers.md`.
- The PDF licensing maintenance decision is recorded in `decisions/0007-pypdf-license-maintenance.md`.
- The Qdrant projection decision is recorded in `decisions/0008-qdrant-knowledge-index.md`.
- The local embedding and chunking decision is recorded in `decisions/0009-local-embedding-and-chunking.md`.
- The grounded OpenAI Responses decision is recorded in `decisions/0010-grounded-openai-responses.md`.
- The structured business-domain decision is recorded in `decisions/0011-structured-business-domain.md`.
- The structured extraction decision is recorded in `decisions/0012-candidate-first-structured-extraction.md`.
- The typed business-tool decision is recorded in `decisions/0013-typed-read-only-business-tools.md`.
- The multi-source routing and secure source-delivery decision is recorded in
  `decisions/0014-bounded-internal-evidence-routing.md`.
- The controlled external web evidence decision is recorded in
  `decisions/0015-controlled-external-web-evidence.md`.
- The deterministic reminder parsing and claim/send ledger decision is recorded in
  `decisions/0016-deterministic-reminders.md`.

## Principles

- Build company-specific business logic; integrate mature commodity infrastructure.
- Keep external providers behind small replaceable interfaces.
- Treat the LLM as a reasoning and synthesis component, never as the authoritative database.
- Add infrastructure only when a current phase has a demonstrated need.

