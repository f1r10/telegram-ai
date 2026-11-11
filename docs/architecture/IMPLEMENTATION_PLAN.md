# Implementation Plan — Global Intelligence Engine

Derived from [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md) and constrained by the measured
baseline in [BASELINE_AUDIT.md](../audit/BASELINE_AUDIT.md) and
[FAILURE_ANALYSIS.md](../audit/FAILURE_ANALYSIS.md).

Phases are executed **in strict order** (§18). A phase does not start until the previous phase's
exit criteria are met *and* recorded in [PROGRESS.md](../progress/PROGRESS.md) with evidence.

Per-phase ritual (§18): Plan → files changed → implementation → tests (`make test`, `make lint`,
`make typecheck`) → result with numbers → docs update → ledger entry.

---

## Phase 0 — Audit, baseline, harness skeleton  ← *current*

**Scope.** Record git state; read the governing documents; inventory code, models, migrations, env
var names; run the suite and record the baseline; reproduce the quality problem with evidence;
write the architecture documents; create the evaluation harness.

**Outputs.** `docs/audit/BASELINE_AUDIT.md`, `docs/audit/FAILURE_ANALYSIS.md`,
`docs/architecture/TARGET_ARCHITECTURE.md`,
`docs/architecture/OPEN_SOURCE_INTEGRATION_MATRIX.md`, `docs/architecture/IMPLEMENTATION_PLAN.md`,
`docs/progress/PROGRESS.md`, `tests/evaluation/`.

**Exit criteria.** Baseline tests recorded ✔ · failure modes identified with evidence ✔ · plan
written ✔ · harness skeleton runs offline ✔.

---

## Phase 1 — Query understanding, planner, orchestration, evidence aggregation

**Target scenarios:** A (typo), I (aliases), K (camera → connectors).

**Files to add**

```text
src/office_assistant/global_intelligence/__init__.py
src/office_assistant/global_intelligence/flags.py
src/office_assistant/global_intelligence/models.py            # QueryUnderstanding, QueryPlan, SubQuestion, SourceType, RetrievalBudget
src/office_assistant/global_intelligence/understanding/normalizer.py
src/office_assistant/global_intelligence/understanding/lexicon.py        # AZ/TR/RU/EN domain vocabulary
src/office_assistant/global_intelligence/understanding/typos.py          # bounded edit-distance + transliteration, code-safe
src/office_assistant/global_intelligence/understanding/concepts.py       # device -> connector/cable hypotheses
src/office_assistant/global_intelligence/understanding/decomposer.py
src/office_assistant/global_intelligence/understanding/language.py
src/office_assistant/global_intelligence/planning/planner.py             # deterministic-first, LLM second
src/office_assistant/global_intelligence/evidence/confidence.py          # StatementStatus
src/office_assistant/global_intelligence/evidence/aggregate.py
src/office_assistant/global_intelligence/engine.py
```

**Files changed (minimal, additive)**

* [internal_evidence/router.py](../../src/office_assistant/internal_evidence/router.py) — the
  hard-coded `internal_markers` tuple moves into `understanding/lexicon.py` and is extended with the
  measured missing vocabulary (kamera, kabel, printer, stok, anbar, təchizatçı, müqavilə, …). Behaviour
  when the flag is off is byte-identical.
* [internal_evidence/models.py](../../src/office_assistant/internal_evidence/models.py) — add the
  `StatementStatus` enum re-export; `EvidencePlan` unchanged.
* [internal_evidence/query_normalization.py](../../src/office_assistant/internal_evidence/query_normalization.py)
  — keep `normalize_user_query`, `retrieval_query`, `strong_identifiers` as public API; the typo layer
  calls them and never bypasses identifier preservation (`strong_identifiers` stays authoritative).

**Non-negotiable in this phase**

* Product codes / model / serial numbers are **never** typo-corrected (§5). `strong_identifiers()`
  runs first and its tokens are excluded from all correction.
* An ambiguous correction produces `clarification_required` with the alternatives instead of a
  silent pick (§5).
* Concept expansion produces **hypotheses**, each carrying `PROBABLE` until corroborated (§5, F3).
* Deterministic fallback: if the LLM planner fails, `deterministic_plan()` answers (§5 last bullet).

**Tests.** `tests/unit/test_gi_understanding.py`, `tests/unit/test_gi_typos.py`,
`tests/unit/test_gi_concepts.py`, `tests/unit/test_gi_planner.py`,
`tests/unit/test_gi_engine_skeleton.py`; harness scenario assertions for A, I, K.

**Exit criteria.** Scenarios A, I, K move from fail → pass in
`docs/evaluation/BENCHMARK_RESULTS.md`; every pre-existing test still passes; no flag left on by
default in the shipped configuration.

---

## Phase 2 — Document intelligence

**Scope.** Parser benchmark (existing vs Docling), OCR benchmark per language, Excel cross-sheet
understanding, PDF table/scan handling, embedded-image extraction, provenance and visible failure
status.

**Files.** `global_intelligence/providers/document_intelligence.py`, `.../ocr.py`; adapters under
`global_intelligence/adapters/`; extend
[processing/parsers.py](../../src/office_assistant/processing/parsers.py) with cross-sheet lookups;
reuse `scripts/evaluate_docling.py`, `scripts/benchmark_ocr.py`.

**Rules.** Partial or failed processing must be visible and never presented as fully processed
(§6). Numbers are computed deterministically, never by the LLM (§6, `AGENTS.md` invariant 3).

**Exit criteria.** Benchmark tables for parser and OCR in
`docs/evaluation/BENCHMARK_RESULTS.md`; adoption or rejection of Docling/PaddleOCR recorded in the
integration matrix; scanned PDF that yields no text surfaces a visible failure.

---

## Phase 3 — Entity resolution, relations, temporal facts

**Target scenarios:** B (cross-document), I (no wrong merges).

**Scope.** New tables `entities`, `entity_aliases`, `entity_candidates`, `entity_relations`,
`entity_relation_evidence` (§6 of the architecture doc). Resolver using the strength order of §8.
Recursive-CTE multi-hop. `valid_from`/`valid_until`.

**Hard rules.** High similarity alone never auto-merges (§8). Ambiguous identity keeps candidates and
routes to the existing human review workflow. **No relation without a source**; a guessed relation is
`inferred`, never `confirmed` (§9).

**Exit criteria.** B and I pass; entity-resolution precision/recall reported; migration is reversible
and was applied to a disposable `*_test` database.

---

## Phase 4 — Multimodal

**Target scenario:** C (printer photo → which cable).

**Scope.** `VisionProvider` / `OCRProvider` implementations, product-image matching via the existing
`processing/semantic_visual.py` + `processing/visual_ann.py`, candidate verification against the
catalog.

**Rules.** Image-derived identity stays a **candidate** until corroborated (§10). Text inside images
is untrusted data. Do not mark image search production-ready without a real-image benchmark (§10).

**Exit criteria.** Scenario C passes with an explicit confidence label; image-retrieval precision
recorded.

---

## Phase 5 — Global reasoning

**Target scenarios:** D (spreadsheet/SQL), E (conflict), F (missing), G (mixed), K (camera
compatibility).

**Scope.** `reasoning/conflicts.py` (typed, dated, reliability-aware — replaces the money-only
`_detect_conflicts`), `reasoning/temporal.py`, `reasoning/engine.py` (multi-hop, constraint checking,
missing-information detection), `reasoning/verification.py` (claim → source, ACL re-check,
downgrade).

**Rules.** Different numbers are not automatically a conflict; classify type/date/context first
(§13.2). Do not expose chain-of-thought (§13.3). Unsupported claims are removed or downgraded (§13.4).
**No free-form LLM SQL** — only `business_tools/registry.py` (§11).

**Exit criteria.** D, E, F, G, K pass; conflict-surfacing rate and hallucination rate recorded
baseline vs after.

---

## Phase 6 — Memory

**Scope.** Conversation context + entity-linked retrieval; five separate memory types (§12);
`/saxla` preserved and audited; explicit save confirmation.

**Exit criteria.** `/saxla` behaviour unchanged (existing tests still pass); "what cable does *this*
need?" resolves from prior turns.

---

## Phase 7 — Hardening

**Scope.** Security review, ACL tests (scenario L), prompt-injection tests (scenario J), resource
limits, provider-outage handling (scenario M), observability, backup/restore rehearsal, live Telegram
acceptance run or explicit NOT VERIFIED.

**Outputs.** `docs/security/SECURITY_REVIEW.md`,
`docs/operations/DEPLOYMENT_AND_ROLLBACK.md`.

---

## Cross-cutting rules for every phase

1. `make test lint typecheck` after every step; fix failures before continuing (`AGENTS.md`).
2. Bot-facing strings only in
   [integrations/telegram/messages.py](../../src/office_assistant/integrations/telegram/messages.py);
   code, comments, docs and commits in English (`AGENTS.md`).
3. Never `docker compose down -v`; never delete a volume; never rewrite Alembic history; never read or
   commit `.env` (§1.1).
4. Small, reviewable commits on `feature/global-intelligence-engine`; never merge to `main`.
5. Anything not tested is marked **NOT VERIFIED** in the ledger and the final report (§2 rule 3).
6. Unresolvable decisions go to [OPEN-QUESTIONS.md](../OPEN-QUESTIONS.md) with a documented default,
   not a silent guess (§2 rule 6).
