# PROGRESS LEDGER — Global Intelligence Engine

Working protocol: `promt.md` §2. One phase at a time; a phase may not start until the previous
phase's exit criteria are met **and** recorded here with evidence. Anything not tested is marked
**NOT VERIFIED**.

- **Repository:** `https://github.com/f1r10/telegram-ai.git`.
- **Authorised feature branch:** `feature/global-intelligence-engine` (never merge to `main`).
- **Base commit:** `e3d04ba654e836d5c2616eac24e26104d7312319` — *"Milestone 3: deliver order reminders
  durably without duplicates (#175)"*.
- **Session date:** 2026-11-12.

> **Isolation note (owner decision, 2026-11-12).** A second agent (TRAE) was concurrently committing
> Phase 0 to the same clone/branch. To avoid clobbering each other, the owner chose to continue in an
> **isolated git worktree**: `c:\Users\User\Downloads\ai_assistand\telegram-ai-qoder` on branch
> **`feature/gie-qoder`**, branched from TRAE's committed Phase 0 (`5d9a398`). All Phase 1+ work below
> is on `feature/gie-qoder`; the owner reconciles the two branches later. Gates run in a separate WSL
> environment `~/telegram-ai-qoder` (own `.venv`, Python 3.12.16). TRAE's Phase 0 record is preserved
> verbatim below as the inherited base.

---

## Phase status

| Phase | Title | Status | Evidence |
| --- | --- | --- | --- |
| 0 | Audit + reproduce the problem + evaluation harness | **COMPLETE — committed `5d9a398` (inherited base)** | `docs/audit/*`, `docs/architecture/*`, `tests/evaluation/*`, this file |
| 1 | Query understanding, planner, orchestration, evidence aggregation | **COMPLETE — offline evidence below** | `src/office_assistant/global_intelligence/*`, `tests/unit/test_gi_*`, `docs/evaluation/BENCHMARK_RESULTS.md` |
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

## Phase 1 — completed steps (branch `feature/gie-qoder`)

### P1.1 What landed (additive, flag-gated)
New package `src/office_assistant/global_intelligence/`:
- `understanding/` — `lexicon.py` (AZ/TR/RU/EN stems, extended internal markers, typo vocabulary,
  device→connector map), `typos.py` (bounded code-safe edit-distance correction), `concepts.py`
  (recognition + PROBABLE connector hypotheses), `language.py` (deterministic language hint),
  `decomposer.py` (bounded multi-topic split), `__init__.py::understand_query` (stage 1 orchestrator).
- `planning/planner.py::plan_query` — deterministic-first: wraps the shipped internal route when it
  already exists; otherwise routes recognized company concepts to internal sources; ambiguous typo →
  clarification; no internal signal → general chat. Only allow-listed tools (`find_product`,
  `resolve_product_price`) are emitted.
- `models.py` — `QueryUnderstanding`, `QueryPlan`, `SubQuestion`, `SourceType`, `RetrievalBudget`,
  `ConceptHypothesis`, `TypoCorrection`; `QueryPlan.to_evidence_plan()` degrades to the shipped type.
- `evidence/confidence.py` — `StatementStatus` (Təsdiqlənib/Ehtimal/Naməlum), `SourceClass`,
  `Statement`, `effective_status` (CONFIRMED needs evidence_id + locator; image ≤ PROBABLE).
- `flags.py` — `EngineFlags` (9 flags, all default `False`) + `from_settings`.
- `engine.py` — `GlobalIntelligenceEngine` skeleton composing understand → plan.

Minimal additive edits to existing files:
- `core/settings.py` — 9 `gi_*` flag fields, all default `False`.
- `internal_evidence/query_normalization.py` — `strong_identifiers(..., *, strip_case_suffix=False)`
  (default byte-identical) + `_strip_case_suffix` helper: drops an AZ case suffix absorbed into a
  code's ASCII tail ("M404dən"→"M404") while preserving a genuine model tail ("M404dn").
- `internal_evidence/router.py` — **UNCHANGED** (byte-identical); the F1/F2/F4 defect reproducers in
  `tests/evaluation/test_evaluation_harness.py` still pass.

### P1.2 Tests (evidence)
- New: `tests/unit/test_gi_{typos,concepts,understanding,planner,engine_skeleton}.py` (40 tests).
- `tests/evaluation/probe.py` now measures `understand_query` + `plan_query` (still offline/deterministic).
- Flipped 4 former `xfail` gates to hard assertions: A/I/K understanding targets + D identifier
  preservation (`test_evaluation_baseline.py`).

### P1.3 Quality gates (WSL `~/telegram-ai-qoder`, offline)
- `uv run --no-sync pytest -m "not integration"` → **1411 passed, 126 deselected, 0 failed** (25 s).
- `pytest tests/unit/test_gi_*.py tests/evaluation` → **70 passed**.
- `ruff check src tests` + `ruff format --check src tests` (= `make lint`) → **clean** (398 files).
  Whole-tree `ruff check .` also flags pre-existing migration/doc lint debt outside the gate; left untouched.
- `pyright` (strict, whole project) → **0 errors, 0 warnings**.
- `python scripts/eval_scorecard.py` → **understanding target 6/7** (A,B,E,F,I,K). Phase 0 was 2/7.
  **A, I, K moved fail → pass** (Phase 1 exit criterion). D is an honest later-phase gap.

Full numbers and the NOT VERIFIED list: `docs/evaluation/BENCHMARK_RESULTS.md`.

### P1.4 Deviations from IMPLEMENTATION_PLAN.md (recorded, not silent)
| Plan item | What was done instead | Why |
| --- | --- | --- |
| `understanding/normalizer.py` | Skipped; `understand_query` calls `normalize_user_query` directly | A wrapper around one existing call adds no value (no speculative abstractions) |
| `evidence/aggregate.py` | Deferred to Phase 5 | Architecture §3.1 marks aggregation "unchanged" (reuses existing `EvidenceBundle`); nothing new to build until retrieval lands |
| Re-export `StatementStatus` from `internal_evidence/models.py` | Homed only in `evidence/confidence.py` | Architecture §4 names confidence.py the single home; a re-export would create a circular import |
| Move `internal_markers` out of `router.py` | Left router byte-identical; extended markers live in `lexicon.py` | Guarantees flag-off behaviour is byte-identical and keeps the defect reproducers green |
| Wire engine into the live handler | Deferred; `engine.py` is a tested skeleton, flags default off | Phase 1 exit criteria are offline fail→pass + no regression; live wiring is a later flag-gated step |

### P1.5 Decisions
| # | Decision | Why |
| --- | --- | --- |
| D5 | New planner trusts the shipped internal route when present; only adds routes for general-chat/LLM-only questions | Additive, lowest-risk fix for F1/F4; preserves E/F and every shipped internal path |
| D6 | D's "printer" concept is NOT inferred from the code M404 in Phase 1 | Code→entity linking is Phase 3 entity resolution; hardcoding M404→printer would overfit the corpus and fabricate knowledge |
| D7 | Probe measures the new layer directly (functions are pure); flags gate live wiring, not measurement | The capability is deterministic and offline-measurable now; production routing stays flag-off until a later phase |

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
- **Push not yet performed.** Phase 0 is committed locally on the feature branch first; a push to
  `origin/feature/global-intelligence-engine` requires network access and is attempted only for the
  feature branch (never `main`).

## Next step
Commit Phase 1 on `feature/gie-qoder` and push to `origin/feature/gie-qoder` (never `main`), then
begin **Phase 2** (document intelligence: parser/OCR adapters + benchmark, cross-sheet Excel,
visible failure status; target scenario H). Phase 1's offline understanding/planner layer is the
foundation; live application wiring remains deferred behind `gi_engine_enabled`.

## NOT VERIFIED
- Any end-to-end answer, retrieval metric, OCR/vision result, graph reasoning result, ACL test or
  provider-outage behaviour: **not measured** — those phases have not landed.
- Live application wiring of the engine (Telegram handler) — deferred, flag-gated, default off.
- `docs/architecture/GLOBAL_INTELLIGENCE_ENGINE.md`, `docs/security/SECURITY_REVIEW.md`,
  `docs/operations/DEPLOYMENT_AND_ROLLBACK.md`: pending their phases.
- `docs/evaluation/BENCHMARK_RESULTS.md`: **created in Phase 1** (offline numbers only).
