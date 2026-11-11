# PROGRESS LEDGER — Global Intelligence Engine

Working protocol: `promt.md` §2. One phase at a time; a phase may not start until the previous
phase's exit criteria are met **and** recorded here with evidence. Anything not tested is marked
**NOT VERIFIED**.

- **Repository:** `https://github.com/f1r10/telegram-ai.git` — present locally at
  `c:\Users\User\Downloads\ai_assistand\telegram-ai` with `origin` set to that URL and the
  authorised branch already checked out.
- **Authorised feature branch:** `feature/global-intelligence-engine` (never merge to `main`).
- **Base commit:** `e3d04ba654e836d5c2616eac24e26104d7312319` — *"Milestone 3: deliver order reminders
  durably without duplicates (#175)"*.
- **Phase 0 commit:** `5d9a398` — *"Phase 0: audit, reproduce the defects, and build the evaluation
  harness"* — committed **and pushed** to `origin/feature/global-intelligence-engine`.
- **Session date:** 2026-11-12.

---

## Phase status

| Phase | Title | Status | Evidence |
| --- | --- | --- | --- |
| 0 | Audit + reproduce the problem + evaluation harness | **COMPLETE** — committed `5d9a398`, pushed | `docs/audit/*`, `docs/architecture/*`, `tests/evaluation/*`, this file |
| 1 | Query understanding, planner, orchestration, evidence aggregation | **COMPLETE** — committed on the feature branch | `src/office_assistant/global_intelligence/*`, `docs/evaluation/BENCHMARK_RESULTS.md`, `tests/unit/test_gi_*.py` |
| 2 | Document intelligence (parser/OCR adapters, Docling benchmark) | NOT STARTED | — |
| 3 | Entity resolution, aliases, relations, temporal facts | NOT STARTED | — |
| 4 | Multimodal vision/OCR providers, candidate verification | NOT STARTED | — |
| 5 | Global reasoning (multi-hop, SQL tools, conflicts, verification) | NOT STARTED | — |
| 6 | Memory (conversation context, entity-linked retrieval, `/saxla`) | NOT STARTED | — |
| 7 | Hardening (security review, ACL, resource limits, failure handling) | NOT STARTED | — |

---

## Phase 0 — completed steps

### P0.1 Git state (recorded)
- Branch `feature/global-intelligence-engine` created from `main` at `e3d04ba6`.
- Working tree before Phase 0: only line-ending (LF→CRLF) noise on `Dockerfile` and
  `src/office_assistant/application/business_common.py`; **zero content changes**. The single
  deliberate pre-existing edit is a Windows-only test fix (see P0.4).

### P0.2 Repository read (governing sources)
- `AGENTS.md` (10 non-negotiable invariants), root `README.md`, `docs/SPEC.md`, `docs/ROADMAP.md`,
  `docs/OPEN-QUESTIONS.md` (Q1–Q49), all 17 ADRs in `docs/architecture/decisions/`.
- Confirmed stack: Python 3.12, PostgreSQL (system of record), Qdrant (rebuildable projection),
  SQLAlchemy async + Alembic, Pydantic v2, python-telegram-bot 21+, FastEmbed, OpenAI Responses,
  pytest / Ruff / Pyright strict. **No Redis, no Celery** — background work is the PostgreSQL `jobs`
  table with `FOR UPDATE SKIP LOCKED`.

### P0.3 Code inventory (summary; full matrix in `docs/audit/BASELINE_AUDIT.md`)
- 79 mapped DB model classes; 27 Alembic migrations; Telegram handlers, worker, ingestion,
  retrieval/routing (`internal_evidence/`), provider abstraction (`ai/`), business tools
  (`business_tools/`), knowledge index (`knowledge/`).
- Feature matrix (works / partial / missing) recorded with proof pointers.

### P0.4 Baseline test run (evidence)
Command: `uv run --no-sync pytest` (host: native Windows).
Result: **1341 collected — 1299 passed, 2 skipped, 40 failed, 0 errors** (64 s).
Every one of the 40 failures is a native-Windows host limitation, not a product defect:
21 POSIX-script/Windows-path, 10 `os.getuid`, 7 symlink privilege (`WinError 1314`), 1 Windows
socket reset, 1 POSIX mode-bits. Grouped with node ids in `docs/audit/BASELINE_AUDIT.md`.

Pre-existing Windows-only test fix kept (only pre-existing file edited in Phase 0):
`tests/unit/test_v2_miniapp_photo.py` — explicit `parametrize` ids so the 4 MiB oversize case does
not blow Windows' 32 767-character environment-variable limit via `PYTEST_CURRENT_TEST`.

### P0.5 Failure analysis (evidence)
`docs/audit/FAILURE_ANALYSIS.md` records 9 failure modes F1–F9 with verbatim probe output, e.g.
`"Plinter üçün kabel var?"`, `"Kamera üçün hansı kabellər var?"` and the mixed three-part question
all route to `GENERAL_CHAT` / `GENERAL_WORLD` today.

### P0.6 Architecture + plan artefacts
- `docs/architecture/TARGET_ARCHITECTURE.md` — 10-stage pipeline, per-stage Pydantic contracts,
  `StatementStatus` 3-state model, `EngineFlags` (all default `False`), new additive DB tables,
  provider protocols, defect→phase→scenario→flag traceability.
- `docs/architecture/OPEN_SOURCE_INTEGRATION_MATRIX.md` — 8-column evaluation of RAGFlow, Docling,
  PaddleOCR, LightRAG, Graphiti, etc., with license/resource facts verified by live web search on
  2026-11-12 and a Sources section. Decisions: Qdrant **KEEP**; Docling/PaddleOCR **EVALUATE
  (Phase 2)** behind adapters; RAGFlow **EVALUATE ONLY, isolated `eval` profile**; LightRAG /
  Graphiti **benchmark-gated**; Tika / pgvector / Unstructured **REJECT**.
- `docs/architecture/IMPLEMENTATION_PLAN.md` — Phase 0–7 with file lists, rules, tests, exit criteria.

### P0.7 Evaluation harness (built FIRST, per §17)
New package `tests/evaluation/`:
- `corpus.py` — deterministic synthetic corpus generated by code (never committed binaries):
  ≥10 PDF, ≥5 DOCX, ≥5 XLSX, 5 scanned, 5 contracts/invoices, duplicate + contradictory groups,
  ≥5 unrelated, 1 prompt-injection fixture, 10 product images with OCR ground truth, AZ/RU/EN content.
- `scenarios.py` — the 13 mandatory scenarios **A–M** with question, language, required source
  groups, expected behaviours, relevance ids, target phase.
- `metrics.py` — S17.3 metric primitives (recall@k, MRR, nDCG@k, citation accuracy/presence,
  unsupported-confirmed rate, label coverage), each documenting its degenerate case.
- `runner.py` — provider-agnostic `AnswerProvider` protocol; `ProductionRoutingProvider` measures the
  **real shipped** `internal_evidence.router.deterministic_plan` and never fabricates answers.
- `probe.py` + `scripts/eval_scorecard.py` — offline query-understanding probe/scorecard.
- `test_evaluation_harness.py` / `test_evaluation_baseline.py` — harness self-tests plus Phase 0
  defect reproducers (F1, F2, F4) asserting the CURRENT broken behaviour as a regression guard.

Harness evidence:
- `uv run --no-sync pytest tests/evaluation -q` → **all pass, 4 xfailed** (the 4 xfails are the
  Phase-1 understanding targets A/I/K, expected at baseline).
- `uv run --no-sync python scripts/eval_scorecard.py` → baseline scorecard, *understanding target met:
  2/7* offline-probe scenarios; F1 confirmed (A, D, K misroute to `GENERAL_WORLD`).

### P0.8 Quality gate after Phase 0 changes (evidence)
- `uv run --no-sync pytest -m "not integration" -q` → **1324 passed, 41 failed, 2 skipped,
  126 deselected, 4 xfailed** (64 s). The 41 failures are the same native-Windows host limitations as
  the 40-test baseline (the +1 is the flaky Windows socket-reset test in
  `tests/unit/test_v2_miniapp_http.py`, which passes on re-run). **No failure is in `tests/evaluation`.**
- `uv run --no-sync ruff check` + `ruff format --check` on `tests/evaluation` and
  `scripts/eval_scorecard.py` → **clean**.
- `uv run --no-sync pyright` → 8 errors, all **pre-existing** `os.getuid` unknown-attribute errors in
  `src/office_assistant/scripts/backup_restic.py` and `verify_restic_backup.py` (Windows host: the
  attribute does not exist). **0 errors in any new Phase 0 file.**

---

## Phase 1 — completed steps

Target: query understanding, deterministic-first planner, orchestration skeleton and evidence
aggregation. Target scenarios **A, I, K** (promt.md §18; `docs/architecture/IMPLEMENTATION_PLAN.md`).

### P1.1 New package `src/office_assistant/global_intelligence/`
- `understanding/` — `normalizer.py` (AZ diacritic folding, identifier protection), `lexicon.py`
  (AZ/TR/RU/EN domain vocabulary, the moved `INTERNAL_MARKERS`, device/connector hypotheses),
  `typos.py` (bounded edit distance, code-safe correction), `concepts.py` (concepts + connector
  hypotheses), `decomposer.py` (multi-topic split), `language.py` (az/ru/en/mixed).
- `planning/planner.py` — deterministic first, model second; `PlanOutcome` records why it stayed
  deterministic; a provider failure degrades to `deterministic_plan` instead of raising (invariant 8).
- `evidence/` — `confidence.py` (`StatementStatus` 3-state) and `aggregate.py`.
- `flags.py` — nine `EngineFlags`, **all default `False`**, master switch gates every stage.
- `engine.py` — `GlobalIntelligenceEngine` (`.from_settings`, `.understand`, `await .plan`, `.aggregate`).

### P1.2 Minimal additive edits to shipped modules
- `internal_evidence/router.py` — the hard-coded marker tuple moves into `understanding/lexicon.py`
  and gains the measured missing vocabulary; a new `_asks_device_or_accessory()` route fixes defect
  **F1**. With the engine disabled the flag-off path is unchanged.
- `internal_evidence/models.py` — `StatementStatus` added.
- `core/settings.py` + `.env.example` — nine `gi_*` flags, all default `False`.

### P1.3 Tests (new)
`tests/unit/test_gi_typos.py`, `test_gi_understanding.py`, `test_gi_concepts.py`, `test_gi_planner.py`,
`test_gi_engine_skeleton.py` — pin the §5 rules: product codes are never typo-corrected, an ambiguous
correction returns alternatives instead of picking, concept expansion yields `PROBABLE` hypotheses, and
the planner policy treats the message as untrusted data.

### P1.4 Harness updated to measure the shipped layer
`tests/evaluation/probe.py` now delegates concept recognition to the shipped `understand()` instead of a
private duplicate stem table (the duplicate could never see `plinter` → `printer`). The two Phase-0
reproducers F1 and F4 were **inverted into regression guards**; the F2 reproducer is **unchanged**
because the `product and qiymet` branch intercepts it before the new device route. The four `xfail`
understanding gates for A/I/K were flipped to hard assertions.

### P1.5 Verification (evidence)
- `uv run --no-sync pytest tests/unit/test_gi_*.py tests/evaluation -q` → **71 collected: 70 passed,
  1 xfailed** (the xfail is D, unchanged by design).
- `uv run --no-sync python scripts/eval_scorecard.py` → offline understanding target **2/7 → 6/7**;
  A/I/K now route `DEVICE_ACCESSORY_LOOKUP` with `allow_general_knowledge = False` (B moved too).
- `ruff check` / `ruff format --check` / `pyright` → **0 errors in any Phase 1 file**; the remaining
  errors (9 E501 migrations, 10 unformatted pre-existing files, 8 `os.getuid` scripts) are pre-existing.
- `pytest -m "not integration"` → the 14 failing files are pre-existing native-Windows limitations,
  **proved** by stashing the Phase 1 work and re-running the same files at the Phase-0 base (identical
  `os.getuid` / `bash` 127 / `WinError 1314` failures). No `global_intelligence` or evaluation test fails.
- Full matrix and gates: `docs/evaluation/BENCHMARK_RESULTS.md`.

---

## Decisions made (also appended to `docs/OPEN-QUESTIONS.md` as Q50–Q52)

| # | Decision | Why |
| --- | --- | --- |
| D1 | Evaluation harness lives in `tests/evaluation/`; corpus is code-generated | §17 mandates the harness first; §2.4 forbids committing binary corpora |
| D2 | New engine paths behind `EngineFlags`, all default `False` | §18 "feature flags so the old behaviour can be restored instantly" |
| D3 | New orchestration package `src/office_assistant/global_intelligence/` | spec names the pipeline, not its module path; keeps `internal_evidence/` intact |
| D4 | Harness measures the shipped function; unmeasurable metrics stay "NOT VERIFIED" | §2.3 evidence over claims; never fabricate live results |

## Blockers / environment honesty (§2.5)
- **No clone was needed.** The authorised repository is already present locally with `origin` =
  `https://github.com/f1r10/telegram-ai.git`, checked out on `feature/global-intelligence-engine` at
  the required base commit. Work proceeds in that working copy; no other repository is touched.
- **NOT VERIFIED live (no live capability):** PostgreSQL, Qdrant, a live Telegram token, an LLM/vision
  API key, a populated real corpus, Docker. All Phase 0 evidence is deterministic and offline.
- Native-Windows host: 41 environment-only test failures (above); a Linux CI/VM run is required to
  confirm those suites.
- **Push:** Phase 0 (`5d9a398`) is pushed to `origin/feature/global-intelligence-engine`. The Phase 1
  commit is pushed to the same feature branch (never `main`); a push failing for network reasons is
  reported rather than retried blindly.

## Next step
Begin **Phase 2 — document intelligence** (parser benchmark, OCR per language, Excel cross-sheet
understanding, Docling behind an adapter), targeting scenarios H and the parser/OCR metrics defined in
`docs/architecture/IMPLEMENTATION_PLAN.md`. Phase 1 is complete and its exit criteria are recorded in
`docs/evaluation/BENCHMARK_RESULTS.md`.

## NOT VERIFIED
- Any end-to-end answer, retrieval metric, OCR/vision result, graph reasoning result, ACL test or
  provider-outage behaviour: **not measured** — those phases have not landed.
- `docs/architecture/GLOBAL_INTELLIGENCE_ENGINE.md`, `docs/security/SECURITY_REVIEW.md`,
  `docs/operations/DEPLOYMENT_AND_ROLLBACK.md`: pending their phases.
- Scenarios C, G, H, J, L, M stay **NOT VERIFIED** offline (they need a live provider/corpus/database).
