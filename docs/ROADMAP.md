# ROADMAP — Implementation Phases (reconciled 2026-10-06)

This file previously listed a 7-phase plan (0-6) that was never checked off and
no longer matches the delivered system. The authoritative phase plan is now the
one actually implemented, recorded in `docs/architecture/README.md`, the decision
records `docs/architecture/decisions/0001..0016`, and the test suite
(`tests/integration/test_phaseN_*`). Completed items are checked only where
acceptance evidence exists in-repo (migrations, modules, tests).

The original SPEC.md remains the requirements source; phases below map to it.

## Phase 0 — Project Setup & Tooling ✅

- [x] Python 3.12 project with uv (`pyproject.toml`, `uv.lock`).
- [x] Docker Compose: app, worker, postgres, qdrant (+ searxng, model-cache-init).
- [x] Linting (ruff) and typing (pyright strict) — pyright replaced mypy in practice; recorded here, AGENTS.md updated to match.
- [x] pytest configuration; unit tests run offline (`-m "not integration"`).
- [x] Structured, redacted logging (`core/logging.py`).
- [x] Makefile: up, down, logs, test, lint, typecheck, fmt, migrate (seed pending, OPEN-QUESTIONS Q14).

## Phase 1 — Telegram Scaffold & Database Foundation ✅

- [x] SQLAlchemy async models, Alembic, migrations 0001-0003 (users, audit).
- [x] Telegram transport (python-telegram-bot, long polling) behind handlers layer.
- [x] `/start`, `/whoami`, `/users`, `/cancel` commands; Azerbaijani UI strings module.
- [x] Allowlist + access-password authentication (`decisions/0003`), role model.

## Phase 2 — Authentication & Raw Ingestion ✅

- [x] Private-chat raw file ingestion with pending save sessions (`decisions/0004`).
- [x] File validation, size limits, local storage abstraction.

## Phase 3 — PostgreSQL Worker ✅

- [x] `jobs` table, lease-based claiming with `FOR UPDATE SKIP LOCKED` (`decisions/0005`).
- [x] Deterministic ingestion worker; per-file and per-job states.

## Phase 4 — Document Parsing ✅

- [x] Parser adapters: TXT, PDF, DOCX, XLSX, deferred image/OCR marking (`decisions/0006`, `0007`).
- [x] Versioned normalized parse artifacts; Telegram notification outbox.

## Phase 5 — Knowledge Index & Retrieval ✅

- [x] Qdrant collection with alias, deterministic chunks, FastEmbed dense + BM25 sparse (`decisions/0008`, `0009`).
- [x] Hybrid RRF retrieval with file/parse-run/media-type filters; access filters applied.
- [x] Migrations 0004-0008.

## Phase 6 — Grounded Answers & Chat ✅

- [x] OpenAI Responses API behind provider abstraction (`decisions/0010`).
- [x] `/baza` grounded answers with validated citations; conversation persistence (migration 0009).

## Phase 7 — Structured Business Data ✅

- [x] Products, customers, prices, payments, events, proposals (migration 0010, `decisions/0011`).

## Phase 8 — Candidate-First Extraction ✅

- [x] Structured extraction with explicit Telegram review (migration 0011, `decisions/0012`).

## Phase 9 — Typed Business Tools ✅

- [x] Read-only, allowlisted PostgreSQL tool registry (migration 0012, `decisions/0013`).

## Phase 10 — Internal Evidence Orchestration ✅

- [x] Bounded natural-language routing across chat, business tools, and document evidence (`decisions/0014`).
- [x] Unified citations, conflict surfacing, secure recent-source file delivery.

## Phase 11 — Controlled External Web Evidence ✅

- [x] SearXNG + Tavily behind `WebSearchProvider` (migrations 0013, `decisions/0015`, ADR-001).
- [x] `web_search_cache`, `/internet`, `/tamaxtar`, SSRF-hardened fetcher, evidence separation.

## Phase 12 — Reminders, /help, /verilerimi_sil ✅ (SPEC §4, §5)

- [x] `reminders` + `reminder_deliveries` tables (migration 0014, `users.timezone` added).
- [x] Azerbaijani natural-language time parsing with injected Clock and user IANA timezone.
- [x] Confirm/Edit/Cancel UX before saving (invariant 9): inline keyboard, token-scoped proposals.
- [x] Worker dispatcher loop with `FOR UPDATE SKIP LOCKED`; delivery ledger state machine
      `claimed -> sending -> sent` / `retry_pending -> failed` driven by the app-side outbox consumer
      (`decisions/0016`).
- [x] `/hatirlatmalar` with inline cancel, `/help`, `/verilerimi_sil` with confirmation keyboard.
- [x] SPEC §5 Azerbaijani golden cases as tests (17 parser cases, 15 Phase 12 UX/ledger cases).

Recurring reminders remain out of scope (`recurrence_rule` column reserved, unused).

## Phase 13 — Live-test fixes and image ingestion (2026-10-06)

- [x] Issue 1: user-facing citation labels no longer render raw locator JSON.
- [x] Issue 2: extraction review failures are no longer reported as permission problems; an aborted
      apply leaves `PARTIAL` instead of a stuck `APPLYING`.
- [x] Issue 3: a command typed in a file/photo caption routes to the command instead of ingestion.
- [x] Issue 4: web search reports outages, logs why an answer said "nothing found", stops forcing a
      one-day window for price questions, and runs two DuckDuckGo paths inside a 12-second budget.
- [x] Issue 5: uploaded JPEG/PNG images are transcribed through vision (`decisions/0017`), indexed
      like any other document, and left visibly `DEFERRED` when the vendor fails.
      *Amended 2026-10-07:* automatic transcription at upload was withdrawn by the ADR 0017
      amendment; images are stored `DEFERRED` and read on demand (Block 3A below).

## Master Spec remediation blocks (2026-10-07, owner-approved order)

- [x] Block 1: retrieval truth — the read path resolves the live projection, a missing or failing
      Qdrant alias/collection is reported as unavailable instead of "nothing found", and local
      embedding models load at startup rather than inside the first user turn.
- [x] Block 2: clarification honesty and `FILE_DELIVERY` reachability — `ver`-family delivery
      verbs are recognised before the evidence planner runs, and a clarification turn states the
      element its reason code actually detected instead of always demanding a customer.
- [x] Block 3A: no vendor egress at upload — the ingestion worker holds no vision client, a JPEG/PNG
      upload ends in a `DEFERRED` `image-probe` run with an empty text artifact, and image reading
      moves to the turn that asks about the image (ADR 0017 amendment).
- [x] Block 3: multimodal router — `TurnContext`/`MediaRef` carry the image attached to an explicit
      command, or the one the user replies to, into the turn as a first-class search parameter, read
      on demand for that question only and cited as `IMAGE_UNTRUSTED` (Master Spec §22, §3.1). The
      caption router no longer discards the file: `/baza` and `/tamaxtar` keep the attachment, and
      `/saxla` stores it first and then runs.
- [ ] Block 4: web fallback and relevance gate (Master Spec §21).
- [ ] Block 5: Phase 12A image-asset foundation (Master Spec §23).

## Explicitly out of scope (recorded): recurring reminders, scanned-PDF OCR, image embeddings,
exchange-rate conversion, reports, reranking, mutation tools, autonomous agents.
