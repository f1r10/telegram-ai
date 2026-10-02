# Architecture Documentation

This directory contains repository-local architecture notes and approved decision records.

The original research report is currently outside this repository at:

`C:\Users\User\Downloads\deep-research-report.md`

That report is reference material. Repository decisions should be recorded here so they are versioned with the code.

## Current status

- Phase 1 persistence foundation is implemented.
- PostgreSQL and Alembic are implemented; Qdrant, Telegram, OpenAI, document ingestion, RAG, and web search are planned, not implemented.
- The approved web-search decision is recorded in `decisions/0001-web-search-v1.md`.
- The persistence decision is recorded in `decisions/0002-postgresql-persistence.md`.
- The Telegram authentication decision is recorded in `decisions/0003-telegram-authentication.md`.
- The raw ingestion decision is recorded in `decisions/0004-raw-file-ingestion.md`.

## Principles

- Build company-specific business logic; integrate mature commodity infrastructure.
- Keep external providers behind small replaceable interfaces.
- Treat the LLM as a reasoning and synthesis component, never as the authoritative database.
- Add infrastructure only when a current phase has a demonstrated need.

