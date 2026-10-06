# Changelog

All notable changes to this project. Phases follow `docs/ROADMAP.md` (reconciled 2026-10-06).

## [Unreleased]

### Phase 12 preparation, Phase 0/11 cleanup (2026-10-06)

- Docker isolation for this working copy: Compose project renamed to
  `qoder_ai_assistand`; containers `qoder_app`, `qoder_worker`, `qoder_postgres`,
  `qoder_qdrant`, `qoder_searxng`, `qoder_model_cache_init`; network `qoder_net`;
  volumes `qoder_*`; host ports hardcoded to 5435 (PostgreSQL), 6335 (Qdrant),
  18085 (SearXNG), all bound to 127.0.0.1. Runs side-by-side with the original
  instance with zero collisions. Fresh database migrated to `0013_web_search_evidence`.
- Added `Makefile` (`up, down, logs, test, test-integration, lint, typecheck, fmt, migrate, seed`).
- Rewrote `docs/ROADMAP.md` to the true delivered plan (Phases 0-11 complete, Phase 12 next).
- Reconciled `AGENTS.md` stack notes with reality (python-telegram-bot + pyright, messages module path).
- Added OPEN-QUESTIONS Q14 (seed script undefined).
- Committed the previously uncommitted, fully green Phase 9-11 work
  (structured business domain, candidate-first extraction, typed business tools,
  internal evidence orchestration, controlled external web evidence: `/baza`,
  `/internet`, `/tamaxtar`, web search cache, migration 0013).

### Verification

- `pytest -m "not integration"`: 120 passed offline.
- `ruff check` / `ruff format --check`: clean. `pyright` (strict): 0 errors.
- `docker compose config`: names and ports as above; `docker compose up -d postgres qdrant searxng`: all healthy alongside the original stack.
