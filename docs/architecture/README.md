# Architecture Documentation

This directory contains repository-local architecture notes and approved decision records.

The original research report is currently outside this repository at:

`C:\Users\User\Downloads\deep-research-report.md`

That report is reference material. Repository decisions should be recorded here so they are versioned with the code.

## Current status

- Phase 0 foundation is implemented.
- PostgreSQL, Qdrant, Telegram, OpenAI, document ingestion, RAG, and web search are planned, not implemented.
- The approved web-search decision is recorded in `decisions/0001-web-search-v1.md`.
- Alembic is intentionally postponed until database models are introduced in Phase 2.

## Principles

- Build company-specific business logic; integrate mature commodity infrastructure.
- Keep external providers behind small replaceable interfaces.
- Treat the LLM as a reasoning and synthesis component, never as the authoritative database.
- Add infrastructure only when a current phase has a demonstrated need.

