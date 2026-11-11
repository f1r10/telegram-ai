# BENCHMARK RESULTS — Global Intelligence Engine

Acceptance evidence for the `feature/global-intelligence-engine` work. Every number here is
produced by a command in this repository and is reproducible offline. Anything that needs a live
provider, a real database or a live Telegram token is marked **NOT VERIFIED** rather than guessed.

- **Harness:** `tests/evaluation/` (scenarios A–M, metrics, provider-agnostic runner).
- **Scorecard:** `uv run --no-sync python scripts/eval_scorecard.py`.
- **Host:** native Windows; `uv run --no-sync pytest -m "not integration"`.

## What the offline probe measures

`tests/evaluation/probe.py` measures the **shipped** query-understanding layer — it calls the same
`office_assistant.global_intelligence.understanding.understand()` and
`internal_evidence.router.deterministic_plan()` the application uses (no private duplicate tables).
For each offline-measurable scenario it reports four things:

| Column | Meaning |
| --- | --- |
| **ids kept** | every `expected_identifier` survives as an exact `strong_identifiers()` token |
| **concepts missing** | expected domain concepts the understanding layer failed to recognise |
| **det. route** | the deterministic route the shipped router picks (`GENERAL_WORLD` = a defect) |
| **general-chat** | `allow_general_knowledge` — `True` means a company question leaked to world knowledge |
| **target** | the scenario's understanding target is met |

A scenario counts as met only when the identifiers survive, no expected concept is missing, the
route is not `GENERAL_WORLD` and general world knowledge is not allowed.

## Phase 1 target — Phase 0 baseline vs Phase 1

| id | baseline route | baseline target | Phase 1 route | Phase 1 target |
| --- | --- | --- | --- | --- |
| A | `GENERAL_WORLD` | ✗ | `DEVICE_ACCESSORY_LOOKUP` | ✓ |
| B | *(none → LLM only)* | ✗ | `DEVICE_ACCESSORY_LOOKUP` | ✓ |
| D | `GENERAL_WORLD` | ✗ | *(none → LLM only)* | ✗ (Phase 5 + F2) |
| E | `PRODUCT_PRICE` | ✓ | `PRODUCT_PRICE` | ✓ |
| F | `DOCUMENT_TERMS` | ✓ | `DOCUMENT_TERMS` | ✓ |
| I | *(none → LLM only)* | ✗ | `DEVICE_ACCESSORY_LOOKUP` | ✓ |
| K | `GENERAL_WORLD` | ✗ | `DEVICE_ACCESSORY_LOOKUP` | ✓ |

**Understanding target met: 2/7 → 6/7.**

Phase 1's mandated exit criteria (scenarios **A, I, K** move fail → pass) are met. **B** moved with
them because it shares the device/accessory route. **D** is intentionally unchanged: it is the
Phase-5 cross-sheet reasoning gap, and its expected identifier `M404` is captured with a trailing
dash (`M404D`) by the pre-existing `product and qiymet` branch — defect **F2**, which the Phase 1
scope does not touch. The `xfail` that recorded D stays in place.

## Full scenario matrix A–M (Phase 1)

| id | phase | status | scenario |
| --- | --- | --- | --- |
| A | 1 | **TARGET MET** | Semantic typo: `plinter` → `printer` |
| B | 3 | **TARGET MET** | Cross-document printer/cable compatibility |
| C | 4 | NOT VERIFIED (needs live capability) | Image: printer photo → matching cable |
| D | 5 | GAP (baseline) | Spreadsheet code/stock on different sheets |
| E | 5 | TARGET MET | Conflict: old 1700 vs new 1800 AZN |
| F | 5 | TARGET MET | Missing warranty: no invention |
| G | 5 | NOT VERIFIED (needs live capability) | Mixed topics in one question |
| H | 2 | NOT VERIFIED (needs live capability) | Multilingual: AZ question, EN technical doc |
| I | 1 | **TARGET MET** | Typo + aliases resolve to one entity, distinct stay distinct |
| J | 7 | NOT VERIFIED (needs live capability) | Prompt injection inside a PDF |
| K | 1 | **TARGET MET** | Camera cables inferred from connectors |
| L | 7 | NOT VERIFIED (needs live capability) | ACL: user A private data never leaks to user B |
| M | 7 | NOT VERIFIED (needs live capability) | Provider outage mid-query |

`NOT VERIFIED` means the scenario cannot be measured without a live provider/corpus/database; it is
not a pass and not a fail. Phase 0's baseline recorded the same 2/7 offline target with A, B, D, I
and K as `GAP (baseline)`.

## Quality gates (Phase 1)

| Gate | Command | Result |
| --- | --- | --- |
| New unit tests | `pytest tests/unit/test_gi_*.py tests/evaluation -q` | **70 passed**, 1 expected `xfail` (D) — 71 collected |
| Understanding target | `python scripts/eval_scorecard.py` | A/I/K (+B) met; 6/7 offline |
| Lint | `ruff check` | 9 pre-existing `E501` in `migrations/versions/0019…`, `0020…` — **0 in Phase 1 files** |
| Format | `ruff format --check` | 10 pre-existing (8 migrations + 2 Phase-0 docs) — **0 in Phase 1 files** |
| Types | `pyright` | 8 pre-existing `os.getuid` errors in `scripts/*restic*.py` — **0 in Phase 1 files** |
| Full offline suite | `pytest -m "not integration"` | 14 failing files, all pre-existing native-Windows limitations (proved by re-running them at the Phase-0 base with the Phase 1 work stashed) |

The 14 pre-existing failures are host limitations, not product defects: `os.getuid` does not exist on
Windows (`test_ops_backup_restic.py`, `test_verify_restic_backup.py`), POSIX `bash` scripts cannot be
invoked by path (`test_photo_staging_rollout.py`, `test_staging_compose_isolation.py`,
`test_staging_visual_activation_script.py`, `test_wsl2_staging_preflight.py`, `test_manual_uat_gate.py`),
and creating symlinks needs a privilege the client does not hold (`WinError 1314`, the `test_v2_*`
and `test_verified_visual_vector_hot_cache.py` cases). No `global_intelligence` or `tests/evaluation`
test fails. A Linux CI/VM run is still required to exercise the POSIX-only suites.

## Phase 2 — M0 measurement harness (metrics only, no feature flag)

Phase-2 measurement lands without changing a single production code path. Fixtures are generated at
test time and never committed (audit risk **R7**); the candidate engines are compared by
`src/office_assistant/scripts/evaluate_docling.py`.

**Corpus — generated by code, not committed.** `tests/evaluation/fixtures.py` builds `simple.pdf`,
`headings.docx`, `multi_sheet.xlsx` and `sample.png` into a pytest temp dir from the existing synthetic
corpus text. The PDF bytes are deterministic, so the sha256 below is a stable corpus identifier:

| field | value |
| --- | --- |
| fixture | `simple.pdf` (781 bytes) |
| corpus `sha256` | `e805acac4d56a24a6336584404c390bd50e52b978c3c0ffdb4bf3d6ba3577520` |
| generator | `tests/evaluation/fixtures.py` (deterministic, no dates; read back with pypdf 6.19.0) |
| well-formedness | `%PDF-` + `%%EOF` and `PdfReader` opens ≥ 1 page; DOCX/XLSX are valid OOXML zips; PNG signature + `IHDR` |

**Native vs candidate — phrase recall on 3 human-verified labels.**

| row | command | engine | samples | native | candidate | host profile |
| --- | --- | --- | --- | --- | --- | --- |
| native (**measured**) | `uv run --no-sync pytest tests/evaluation/test_document_fixtures.py -q` | `PdfParser` (pypdf 6.19.0) | 3 | **1.0** | — | native Windows, Python 3.12, CPU |
| Docling arm | `uv run --no-sync python -m office_assistant.scripts.evaluate_docling --pdf <simple.pdf> --truth <labels.json> --local-models <dir> --evaluate` | Docling | — | — | **NOT MEASURED** | Docling is not installed on this host, so the run emits `{"status": "UNAVAILABLE", "content_disclosed": false}` |
| pdfplumber arm | same command (the third arm) | pdfplumber | — | — | **NOT MEASURED** | pdfplumber is not installed on this host; the arm reports `pdfplumber_not_installed` and the two-arm output is unchanged |

The **native** row is a real offline number: the shipped parser recovers all three phrases from the
PDF it was generated from. Both candidate arms are **NOT MEASURED** for the same reason — neither
Docling nor pdfplumber is an installed dependency, and installing either needs an owner/ADR decision
(`docs/phase-2/DEPENDENCY_REVIEW.md`). Nothing here is a production dependency and no number is a
threshold (`TEST_STRATEGY.md` §5 fixes no accuracy gate in Phase 2).

The third arm is reported **additively**: `pdfplumber_phrase_recall` appears only when that arm ran, so
the `DRY_RUN`/`UNAVAILABLE`/`EVALUATED` statuses and the two original recall keys are unchanged
(assumption recorded as `Q53` in `docs/OPEN-QUESTIONS.md`).

**Table and reading-order scorers — report only.** `tests/evaluation/metrics.py` adds
`score_table_cells` (cell precision/recall/F1 keyed by `(row, column)`) and `score_reading_order`
(exact-sequence match plus shared-pair order agreement), in the `benchmark_ocr.py` style where an empty
denominator reports `None`. The M0 evidence is unit-case self-tests, **not** a corpus result: M3
(two-column PDF) and M4 (ruled table) supply the truth grids.

## Not measured (honest gaps)

- End-to-end answers, retrieval quality (recall/MRR/nDCG), OCR and vision accuracy, graph reasoning,
  ACL isolation, provider-outage behaviour: **not measured** — those layers are Phases 2–7.
- Phase 2 native-vs-candidate parser accuracy on real contracts, table cell F1 and reading-order
  agreement on a real corpus: **not measured** — M0 proves the harness generates real bytes and scores
  them; the Docling and pdfplumber arms are unavailable on this host and the table/reading-order truth
  grids arrive with M3/M4.
- No live provider, PostgreSQL, Qdrant, Telegram token or real corpus was used. Every figure above is
  deterministic and offline.
