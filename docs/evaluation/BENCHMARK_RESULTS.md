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

## Phase 2 — M4 PDF table extraction (measured, off by default)

Phase-2 M4 detects bordered and borderless tables, extracts their cells and records them **inside
`DocumentStructure.tables[]`**; the flat `blocks[]` is untouched (see Q58). The feature stays behind
`gi_document_intelligence_enabled=False`.

**Corpus — generated by code, not committed.** `tests/evaluation/fixtures.py` writes ten table
fixtures from hand-built PDF bytes (non-ASCII cell text rides a Type0 / Identity-H font whose
`/ToUnicode` map is the identity, so `Məhsul`, `Şəkər` and `Сахар белый` survive extraction). The
bytes are deterministic, so the digest is a stable corpus identifier:

| field | value |
| --- | --- |
| fixtures | `bordered_table.pdf`, `borderless_table.pdf`, `merged_cells.pdf`, `empty_cells.pdf`, `azerbaijani_table.pdf`, `multilang_table.pdf`, `multiple_tables.pdf`, `multi_page_table.pdf`, `not_a_table.pdf`, `scanned_table.pdf` |
| corpus `sha256` | `3feb82b35440b11a54a28c0294abd4e06260693e04cfa15d58380a09d30c6e52` (over the ten names + bytes, in name order) |
| generator | `tests/evaluation/fixtures.py::generate_table_fixtures` (deterministic, no dates) |
| truth | 11 tables / 104 cells declared by the generator; `not_a_table.pdf` and `scanned_table.pdf` declare **0** tables |

**Native detector — measured per fixture** (`uv run --no-sync pytest tests/evaluation/test_table_extraction.py -q`;
`PdfParser(capture_layout=True)` + `build_document_structure`, pypdf 6.19.0, native Windows, Python
3.12, CPU):

| fixture | tables | cells | detection P / R | cell P / R / F1 | cell text | shape | locator | false positives |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bordered_table.pdf` | 1 | 12 | 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| `borderless_table.pdf` | 1 | 12 | 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| `merged_cells.pdf` | 1 | 7 | 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| `empty_cells.pdf` | 1 | 9 | 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| `azerbaijani_table.pdf` | 1 | 9 | 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| `multilang_table.pdf` | 1 | 9 | 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| `multiple_tables.pdf` | 2 | 21 | 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| `multi_page_table.pdf` | 3 | 25 | 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| `not_a_table.pdf` | 0 | 0 | — | — | — | — | — | 0 |
| `scanned_table.pdf` | 0 | 0 | — | — | — | — | — | 0 |
| **aggregate** | **11** | **104** | **1.0 / 1.0** | **1.0 / 1.0 / 1.0** | **1.0** | **1.0** | **1.0** | **0** |

`merged_cells.pdf` recovers both merged regions (the truth grid has 7 anchor cells, not 9) and
`multi_page_table.pdf` reports three tables with locators `(1,1)`, `(2,1)`, `(3,1)` — pages are never
silently joined. `empty_cells.pdf` keeps all nine positions including the empty ones, so no cell is
dropped. `not_a_table.pdf` is ordinary two-column prose and produces no table; `scanned_table.pdf` is
a **rules-only page with no extractable text**, an honest stand-in for a raster scan — with no OCR
wired in, the shipped detector reports nothing and adds the existing `requires_ocr` warning rather
than inventing a table.

**Candidate arm — pdfplumber 0.11.10, same fixtures, same scorer** (installed into the development
virtual environment only to take the measurement; `pyproject.toml` and `uv.lock` were **not**
changed, and the arm is reached only through `scripts/evaluate_docling.py::pdfplumber_tables`):

| fixture | detection P / R | cell P / R / F1 | cell text | shape | locator | false positives |
| --- | --- | --- | --- | --- | --- | --- |
| `bordered_table.pdf` | 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| `borderless_table.pdf` | n/a / **0.0** | — | — | — | — | 0 |
| `merged_cells.pdf` | 1.0 / 1.0 | **0.7778** / 1.0 / **0.875** | 1.0 | 1.0 | 1.0 | 0 |
| `empty_cells.pdf` | 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| `azerbaijani_table.pdf` | 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| `multilang_table.pdf` | n/a / **0.0** | — | — | — | — | 0 |
| `multiple_tables.pdf` | 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| `multi_page_table.pdf` | 1.0 / 1.0 | 1.0 / 1.0 / 1.0 | 1.0 | 1.0 | 1.0 | 0 |
| `not_a_table.pdf` | — | — | — | — | — | 0 |
| `scanned_table.pdf` | **0.0** / — | — | — | — | — | **1** |
| **aggregate** | **0.9 / 0.8182** | 0.9881 over matched tables | 1.0 | 1.0 | 1.0 | **1** |

**Dependency decision — pdfplumber is NOT adopted.** `docs/phase-2/DEPENDENCY_REVIEW.md` §8 requires a
*measured* win before any dependency change. On these fixtures the shipped native detector (zero new
dependencies) already reaches 1.0/1.0/1.0 with no false positives, while pdfplumber misses both
borderless tables (its default strategy reads drawn lines only), loses 0.125 cell F1 on the merged
fixture, and reports one extra table on the text-free ruled page. So the arm stays
benchmark-only behind the `pdfplumber_not_installed` label; `pyproject.toml` and `uv.lock` are
unchanged, and a two-arm run is byte-for-byte what it was before (Q53). The M0 phrase-recall row above
records the earlier state in which pdfplumber was not installed at all.

**Limitations, stated rather than hidden.**

- The two borderless results on the candidate arm are with pdfplumber's **default** table strategy;
  its text-based strategy was not tuned, so this is a default-configuration result, not the best
  pdfplumber could do. The native comparison is also on a corpus the native detector was developed
  against, so it is an upper bound for the native arm on this fixture family — not evidence about
  real contracts.
- The fixtures use a **uniform** glyph advance (`/DW 500`) and no `cm` transform; a real document has
  per-glyph widths and transforms, so cell geometry here is a model. An earlier `/DW 1000` run split
  characters across cell boundaries and depressed every cell number; the uniform-width fixture was
  corrected before the measurement above.
- **Scanned tables are not read.** `scanned_table.pdf` is a drawn grid with no extractable text; the
  number above is "nothing extractable", not "OCR failed". A real raster scan is reached only through
  the M1 OCR seam and has no table measurement yet — pdfplumber reading it as a 3×2 table of empty
  cells is exactly the false positive the native detector avoids by requiring text.
- Cross-page table continuation is deliberately **not** joined (Q58), so a real multi-page table is
  reported as one table per page.
- No threshold is asserted: these are report-only numbers (TEST_STRATEGY.md §5), and no real
  Azerbaijani/Russian/Turkish labelled corpus was used.

## Not measured (honest gaps)

- End-to-end answers, retrieval quality (recall/MRR/nDCG), OCR and vision accuracy, graph reasoning,
  ACL isolation, provider-outage behaviour: **not measured** — those layers are Phases 2–7.
- Phase 2 native-vs-candidate parser accuracy **on real contracts** is **not measured** — M0 proves the
  harness generates real bytes and scores them, and M4 supplies measured table numbers on generated
  fixtures; the real Azerbaijani/Russian/Turkish labelled corpus, real scanned tables and real
  multi-page contracts are still outstanding. The Docling and pdfplumber *phrase-recall* arms remain
  unmeasured (Docling is not installed; pdfplumber is benchmark-only and, per the M4 decision above,
  not adopted).
- No live provider, PostgreSQL, Qdrant, Telegram token or real corpus was used. Every figure above is
  deterministic and offline.
