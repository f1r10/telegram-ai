# Benchmark Results — Global Intelligence Engine

Evidence ledger for the evaluation harness (promt.md §17). Every number here was produced by an
offline, deterministic command on the WSL gate environment (`~/telegram-ai-qoder`, Python 3.12.16,
`uv run --no-sync`). Anything that needs a live provider, PostgreSQL, Qdrant, Telegram or a real
corpus is marked **NOT VERIFIED** rather than estimated.

- **Branch:** `feature/gie-qoder` (isolated worktree; base commit `5d9a398`, Phase 0).
- **Measurement date:** 2026-11-12.

---

## 1. Query-understanding scorecard (offline probe)

Command: `uv run --no-sync python scripts/eval_scorecard.py`

The probe measures the deterministic understanding + planner surface. **Phase 0** measured the
*shipped* `internal_evidence.router.deterministic_plan` (the guaranteed fallback). **Phase 1**
measures the new `global_intelligence` `understand_query` + `plan_query`, which are also fully
deterministic and offline. The shipped router is left byte-identical; its defect reproducers in
`tests/evaluation/test_evaluation_harness.py` (F1/F2/F4) still pass.

| Scenario | Phase | Phase 0 target | Phase 1 target | Phase 1 route | Notes |
| --- | --- | --- | --- | --- | --- |
| A | 1 | fail | **PASS** | ACCESSORY_COMPATIBILITY | "plinter" → "printer"; printer+cable recognized |
| B | 3 | fail | **PASS** | ACCESSORY_COMPATIBILITY | code M404 preserved; printer+cable |
| D | 5 | fail | gap | STOCK_QUANTITY | identifier fixed (M404dən→M404), stock recognized; **printer concept still missing** |
| E | 5 | pass | **PASS** | PRODUCT_PRICE | shipped route preserved unchanged |
| F | 5 | pass | **PASS** | DOCUMENT_TERMS | shipped route preserved unchanged |
| I | 1 | fail | **PASS** | ACCESSORY_COMPATIBILITY | typo + code M404; printer+cable |
| K | 1 | fail | **PASS** | ACCESSORY_COMPATIBILITY | camera+cable; connectors inferred (PROBABLE) |

**Understanding target met: Phase 0 = 2/7 (E, F) → Phase 1 = 6/7 (A, B, E, F, I, K).**

Phase 1 exit scenarios **A, I, K moved fail → pass.**

### D — the one remaining offline gap (honest)

D = `"M404dən anbarda neçə ədəd qalıb?"`. Phase 1 fixed the identifier defect (`M404dən` → `M404`,
previously `M404D`), recognizes the `stock` concept, and routes to `STOCK_QUANTITY`
(POSTGRES + DOCUMENT, `find_product`). It still does **not** recognize the `printer` concept,
because the question contains no device word — linking the code `M404` to the canonical entity
"printer" requires the entity registry (**Phase 3**). The exact stock number requires the
structured reasoning stage (**Phase 5**). D is therefore a later-phase gap, not a Phase 1 failure;
it is recorded here rather than hidden.

---

## 2. Quality gates (WSL, offline)

| Gate | Command | Result |
| --- | --- | --- |
| Full suite | `uv run --no-sync pytest -m "not integration"` | **1411 passed, 126 deselected, 0 failed** (25 s) |
| GI + evaluation | `pytest tests/unit/test_gi_*.py tests/evaluation` | **70 passed** |
| Lint | `uv run --no-sync ruff check src tests` (= `make lint`) | **All checks passed** |
| Format | `uv run --no-sync ruff format --check src tests` (= `make lint`) | **398 files already formatted** |
| Types | `uv run --no-sync pyright` (strict, whole project) | **0 errors, 0 warnings** |

The project lint gate is scoped to `src tests` (see `Makefile`). A whole-tree `ruff check .`
additionally surfaces pre-existing E501/format debt in Alembic migrations (`0015`–`0025`) and two
Phase-0 design docs; those are outside the gate, predate Phase 1, and are intentionally left
untouched (promt.md forbids rewriting migration history; AGENTS.md forbids out-of-scope refactors).

Baseline before Phase 1 (WSL): 1367 passed. Phase 1 added 40 GI unit tests and flipped 4 former
`xfail` gates (A/I/K understanding + D identifier preservation) to hard passing assertions:
1367 + 40 + 4 = 1411. **No pre-existing test regressed.**

The 41 native-Windows host failures recorded in Phase 0 (`os.getuid`, symlink privilege, POSIX
scripts) do not occur on the WSL/Linux gate; pyright reports 0 errors there (the 8 Phase-0
`os.getuid` errors were a Windows-host artifact).

---

## 3. Defect → phase → scenario status

| Defect | Phase 1 status | Evidence |
| --- | --- | --- |
| F1 company vocabulary misroutes to general chat | **fixed (offline)** | A/K now ACCESSORY_COMPATIBILITY, not GENERAL_CHAT |
| F2 no typo tolerance / identifier contamination | **fixed (offline)** | "plinter"→"printer"; M404dən→M404; M404dn preserved |
| F3 no concept expansion | **hypotheses added** | device→connector ConceptHypothesis, all PROBABLE |
| F4 no decomposition | **fixed (offline)** | G splits into 3 sub-questions (unit test) |
| F5 conflicts money-only | NOT STARTED (Phase 5) | — |
| F6 per-statement confidence | model added, not wired | `StatementStatus` + `effective_status` (Phase 5 wires it) |
| F7 entity registry | NOT STARTED (Phase 3) | D printer-concept gap |
| F8 temporal facts | NOT STARTED (Phase 3) | — |
| F9 evaluation harness | built (Phase 0), extended | this file |

---

## 4. NOT VERIFIED (no live capability in this environment)

The following are **not measured** and must not be claimed as working:

- End-to-end answers, retrieval metrics (recall@k, MRR, nDCG@k), citation accuracy on a real corpus.
- OCR / vision / image product matching (scenarios C, H).
- Graph / multi-hop reasoning, conflict surfacing, temporal facts (D, E, F, G, K full acceptance).
- ACL / cross-user isolation (L), prompt-injection defence (J), provider-outage survival (M).
- Any PostgreSQL, Qdrant, live Telegram token, LLM/vision API key, or Docker behaviour.
- Live application wiring: the engine is **not** connected to the Telegram handler in Phase 1;
  `gi_engine_enabled` and `gi_query_understanding_enabled` default `False`, so the shipped path is
  byte-identical. Wiring is deferred to a later phase behind the flag with a recorded delta.

Scenarios C, G, H, J, L, M remain **NOT VERIFIED** until their phases land with live evidence.