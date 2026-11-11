# Phase 2 — Implementation Plan (milestones)

**Status:** planning only. No code changed, nothing installed, no schema migration, no `gi_*` flag
activated. Every milestone below is **independently testable** and **independently revertable**.

**Companion documents:** [ARCHITECTURE.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/ARCHITECTURE.md) ·
[TEST_STRATEGY.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/TEST_STRATEGY.md) ·
[DEPENDENCY_REVIEW.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/DEPENDENCY_REVIEW.md)

**Date:** 2026-11-12 · **Branch:** `feature/global-intelligence-engine` · **Never merge to `main`.**

---

## 0. Phase 2 definition of done

From the repository plan ([IMPLEMENTATION_PLAN.md#L84-L100](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/architecture/IMPLEMENTATION_PLAN.md#L84-L100)):

1. Benchmark tables for parser and OCR exist in `docs/evaluation/BENCHMARK_RESULTS.md`.
2. Adoption **or rejection** of Docling / PaddleOCR is recorded in
   [OPEN_SOURCE_INTEGRATION_MATRIX.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/architecture/OPEN_SOURCE_INTEGRATION_MATRIX.md).
3. A scanned PDF that yields no text surfaces a **visible failure** (already true; must stay true).

Plus the owner's constraints: no `gi_*` flag activated, no schema change, no dependency installed
without approval, Phase 1 unbroken, every capability behind a flag defaulting `False`.

**Standing rules for every milestone**

- One flag guards the whole feature; it defaults `False`; flag-off behaviour is byte-for-byte the
  pre-Phase-2 pipeline.
- `make test lint typecheck` must pass before the milestone is called done (AGENTS.md).
- New output is **additive**; existing locator keys and `stable_chunk_id()` never change.
- Every milestone ships its own tests *and* an inverted regression guard (see
  [TEST_STRATEGY.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/TEST_STRATEGY.md) §6).

---

## 1. Milestone sequence

```
M0 corpus+harness ─► M1 OCR seam ─► M2 structure layer ─► M3 reading order/furniture
        │                                     │                     │
        │                                     └──► M4 PDF tables ───┤
        ├──► M5 XLSX 2nd pass ───────────────────────────────────────┤
        ├──► M6 DOCX structure ──────────────────────────────────────┤
        ├──► M8 confidence (records only) ───────────────────────────┤
        ├──► M9 resource limits/isolation ───────────────────────────┤
        ├──► M10 embedded-image OCR ─────────────────────────────────┤
        └──► M11 benchmark + adoption decision ◄─────────────────────┘
                             └──────────► M7 cross-sheet representation
```

Ordering rules: **M0 and M1 come first** (measurement and the seam). M2 precedes M3/M4/M7 because
they emit structure. M11 is last because it needs M0's corpus and the M3–M10 measurements.

---

### M0 — Measurement harness and fixture corpus

**Goal.** Make parser/OCR measurement possible at all, without changing production behaviour.

**Scope.**
- Extend `tests/evaluation/corpus.py` (or a sibling) with **deterministically generated binary
  fixtures** — a PDF, a DOCX, an XLSX and a PNG built at test time into a temp dir from the existing
  synthetic text — so the repository stays binary-free (audit risk **R7**).
- Extend [evaluate_docling.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/scripts/evaluate_docling.py)
  to accept a **third arm** (e.g. pdfplumber) without changing its JSON contract or its
  privacy properties.
- Add a small table-level scorer (cell precision/recall/F1) and a reading-order scorer, following the
  existing `benchmark_ocr.py` metric style.
- Register results in `docs/evaluation/BENCHMARK_RESULTS.md` with corpus `sha256`, exact command,
  engine version and host profile.

**Files:** `tests/evaluation/*` (new fixtures + tests), `src/office_assistant/scripts/evaluate_docling.py` (arm only), `docs/evaluation/BENCHMARK_RESULTS.md`.

**Acceptance criteria.**
- A test generates a real PDF/DOCX/XLSX/PNG from code and asserts the bytes are well-formed
  (`%PDF-` header, valid OOXML zip, PNG signature) — **no binary committed**.
- `evaluate_docling.py` still emits `DRY_RUN`/`UNAVAILABLE`/`EVALUATED` exactly as before, and never
  prints private labels.
- `docs/evaluation/BENCHMARK_RESULTS.md` contains at least the **native-vs-candidate** row produced
  by a real run, or explicitly states `NOT MEASURED` with the reason.
- Full suite green: `make test lint typecheck`.

**Flag:** none (measurement only). **Rollback:** delete the added fixtures/tests.

---

### M1 — `OCRProvider` seam (behaviour-preserving refactor)

**Goal.** Introduce the missing seam (gap **G2**) **without changing any OCR behaviour**.

**Scope.** Define `OCRProvider` (ARCHITECTURE §5) at the reserved path
`global_intelligence/providers/ocr.py`; move the existing argument-vector Tesseract invocation behind a
`TesseractOcrProvider`; make `PdfOcrService` / `ImageOcrService` call the provider. Caps, timeouts,
`--psm` values, language regex and language set stay identical.

**Files:** `global_intelligence/providers/ocr.py` (new), `processing/pdf_ocr.py`, `processing/image_ocr.py`, `processing/parsers.py` (injection points only).

**Acceptance criteria.**
- The existing OCR tests pass **unchanged** against the refactor (proof of behaviour preservation).
- An inverted regression test proves that a stubbed provider returning fixed text produces exactly
  the same `page_ocr` blocks and locators as before.
- A provider that raises makes the run `DEFERRED` with a warning — **not** a crash.
- `supported_languages` reflects the image truthfully (today: `{aze, eng, rus}`; **not** `tur`).
- `make test lint typecheck` green; `git diff` shows **no** change to caps, timeouts or language sets.

**Flag:** none required for the refactor (behaviour identical); the *engine-selection* switch is added
in M11 only if a measured alternative exists. **Rollback:** revert the three files.

---

### M2 — Document normalization and structured IR

**Goal.** Add the shared document-structure object (gap **G12**) additively.

**Scope.** Implement `DocumentStructure` and its normalizer (ARCHITECTURE §6). The flat
`blocks[]` list is untouched; `structure` is written as a **new key** in `normalized.json`.

**Files:** `processing/structure.py` (new), `processing/models.py` (additive field/keys only), the parse-run writer, `global_intelligence/flags.py` + `core/settings.py` (**one** new flag).

**Acceptance criteria.**
- With the flag **off**, `normalized.json` is byte-for-byte the pre-M2 artifact (golden test).
- With the flag **on**, `structure` is present and **deterministic**: normalizing the same document
  twice yields identical JSON.
- `chunk_normalized_document` output is **unchanged** by the presence of `structure` (golden chunk
  test, same `chunk_id`s).
- An inverted guard asserts that a document *without* `structure` still chunks exactly as before.

**Flag:** `gi_document_intelligence_enabled` (new, `False`). **Rollback:** flip the flag; the key is
additive and harmless.

---

### M3 — PDF reading order and page furniture

**Goal.** Fix the silent interleaving (**G7**) and index only the body stream (**G8**).

**Scope.** Deterministic geometric ordering; repeated-band and page-number detection emitted as
`block_type="page_furniture"` with `metadata={"repeated_on": [...]}`; furniture excluded from body
retrieval text but **never deleted** from the artifact.

**Files:** `processing/parsers.py` (`PdfParser`), `processing/structure.py`.

**Acceptance criteria.**
- A generated two-column PDF yields a reading order matching the truth sequence (M0's scorer).
- A generated N-page PDF with a repeating footer emits **one** furniture block per repeated line,
  and the body text no longer contains it, while the artifact still does.
- Single-column, single-page PDFs produce output identical to pre-M3 (golden test) — no regression.
- `requires_ocr` / warning behaviour unchanged.

**Flag:** `gi_document_intelligence_enabled`. **Rollback:** flag off ⇒ pre-M3 output.

---

### M4 — PDF table extraction (benchmark-gated)

**Goal.** Close the largest functional gap (**G1**).

**Scope.** Add a `table` block (text = deterministic grid render; `metadata.cells` = row/col/text;
`locator = {"page", "table_index"}`) using the candidate **chosen by M0/M11's measurement**. If the
measured winner is a new dependency, this milestone **cannot ship** until the dependency is approved
(see [DEPENDENCY_REVIEW.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/DEPENDENCY_REVIEW.md)).

**Files:** `processing/parsers.py`, `processing/structure.py`; possibly one dependency (approval-gated).

**Acceptance criteria.**
- Cell-level precision/recall/F1 reported on the M0 corpus with the exact command and versions.
- The existing `page_text` block is **still** emitted (additive), so current citations do not break.
- An inverted guard proves that a PDF **without** tables produces byte-for-byte pre-M4 output.
- A deliberately malformed table region produces a warning, not a crash.

**Flag:** `gi_document_intelligence_enabled` (+ a table sub-switch if M0 shows it is needed).

---

### M5 — XLSX cached values and merged cells

**Goal.** Make computed values available **without** losing formulas (**G5**).

**Scope.** Second `data_only=True` read; `metadata.values` / `metadata.formulas` kept side by side;
merged-region and explicit table-region metadata. `{sheet, range}` locator unchanged.

**Files:** `processing/parsers.py` (`XlsxParser`), `processing/structure.py`.

**Acceptance criteria.**
- A generated workbook with a formula cell records **both** the formula string and the cached value,
  labelled, with neither replacing the other.
- `metadata.formulas` for a formula-only file is identical to pre-M5 (golden test).
- The strict business intakes (`inventory_xlsx.py`, `sales_import.py`) are provably unaffected —
  their tests pass unchanged, and they still **reject** formulas.
- A file whose cached values are missing (`data_only=True` returns `None`) records a warning, not a
  fabricated zero.

**Flag:** `gi_document_intelligence_enabled`.

---

### M6 — DOCX structure

**Goal.** Read hierarchy/list/metadata that python-docx already exposes (**G9**), no new dependency.

**Scope.** Heading level from `outlineLvl`/style depth; lists from `w:numPr`; table **cell
coordinates**; `core_properties` into `ParsedDocument.metadata`.

**Files:** `processing/parsers.py` (`DocxParser`), `processing/structure.py`.

**Acceptance criteria.**
- A generated DOCX with three heading levels yields a correct outline tree with levels.
- A numbered list yields `list_item` blocks with level and marker.
- `ParsedDocument.metadata` is populated and the existing `paragraph`/`table_row` blocks still exist.
- A DOCX with no headings/lists produces output identical to pre-M6 apart from the metadata key
  (golden test with the difference asserted explicitly).

**Flag:** `gi_document_intelligence_enabled`.

---

### M7 — Cross-sheet relationship representation

**Goal.** Express cross-sheet structure with provenance (**G6**) — **represent, do not answer**
(audit open question #4).

**Scope.** Deterministic `relations[]` edges in `DocumentStructure`: source locator → target locator,
with the cue that produced the edge (shared key column, formula reference, defined name). Read-only.

**Files:** `processing/structure.py`, `processing/parsers.py` (`XlsxParser` verification only).

**Acceptance criteria.**
- A generated two-sheet workbook with a shared key column produces a relation whose both endpoints
  carry valid locators (`{sheet, range}`) resolving to real cells.
- No relation is invented when no cue exists (negative test).
- `business_tools` SQL is **not** duplicated and no free-form LLM SQL is introduced (invariant §11).
- With no relations present, output is unchanged (golden test).

**Flag:** `gi_document_intelligence_enabled`.

---

### M8 — Confidence recording

**Goal.** Record confidence without acting on it (**G3**).

**Scope.** `OCRProvider` per-page confidence recorded in `ContentBlock.metadata["confidence"]` and in
the run's derived artifact; native text records `extraction: "native"` and **no** fabricated score.
Aggregate on the parse run.

**Files:** `processing/structure.py`, `processing/pdf_ocr.py` / `image_ocr.py` (via provider), `processing/parsers.py`.

**Acceptance criteria.**
- An OCR page records the engine's confidence; a native-text page records none (asserted, not `1.0`).
- `DocumentChunk.text` is **unchanged** — confidence never enters retrieval text (golden chunk test).
- No threshold is applied anywhere (grep proves no new comparison against a confidence constant).
- Confidence is present only with the flag on.

**Flag:** `gi_document_intelligence_enabled` (recording only; consumption is Phase 5).

---

### M9 — Resource limits and processing isolation

**Goal.** Add a per-parse ceiling and a killable execution boundary (**G11**).

**Scope.** Configurable wall-clock budget, memory ceiling, and a bounded/isolated execution unit for
long extraction; defaults equal today's values.

**Files:** `processing/parsers.py` (parse boundary), `core/settings.py` (limits), possibly a small
isolation helper module.

**Acceptance criteria.**
- A generated "expensive" document aborts at the budget with a **visible warning** and a
  `DEFERRED`/`FAILED` status — never a hang, never a silent success.
- A hostile decompression-style fixture cannot exceed the ceiling (asserted via the abort path).
- Defaults reproduce current limits exactly (golden settings test).
- Documentation states plainly that this is an **availability boundary, not a security sandbox**.

**Flag:** limits are config with safe defaults; the enforcement path is behind
`gi_document_intelligence_enabled`.

---

### M10 — Embedded-image OCR wiring

**Goal.** Make a scanned picture inside a PDF/DOCX/XLSX searchable (**G10**).

**Scope.** Route already-extracted `EmbeddedImage` payloads through the same `OCRProvider` (M1),
gated by the existing `IMAGE_OCR_ENABLED` and byte caps (8 MiB / 200 images, parsers.py:40–41).

**Files:** `processing/parsers.py`, `processing/image_ocr.py`, `processing/structure.py`.

**Acceptance criteria.**
- A generated DOCX/PDF with an embedded text image yields additional OCR text blocks whose locators
  point at the **original** image position (`{page,image_index,xobject}` / `{sheet,...,anchor_cell}`).
- Caps still refuse oversized/too-many images with a warning.
- Documents with no embedded images produce identical output (golden test).
- A provider failure leaves the embedded image stored but **not** silently claimed as text.

**Flag:** `gi_document_intelligence_enabled` **and** the existing `IMAGE_OCR_ENABLED`.

---

### M11 — Benchmark report and adoption decision

**Goal.** Convert measurements into a recorded decision (Phase-2 exit criteria 1 and 2).

**Scope.** Fill `docs/evaluation/BENCHMARK_RESULTS.md` from real runs (B1–B9 in
[PARSER_BENCHMARK.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/PARSER_BENCHMARK.md) §5);
record the Docling / PaddleOCR **adopt-or-reject** outcome in
[OPEN_SOURCE_INTEGRATION_MATRIX.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/architecture/OPEN_SOURCE_INTEGRATION_MATRIX.md);
write an ADR for any adopted engine; add a `CHANGELOG.md` entry.

**Files:** `docs/evaluation/BENCHMARK_RESULTS.md`, `docs/architecture/OPEN_SOURCE_INTEGRATION_MATRIX.md`, a new ADR if adopted, `CHANGELOG.md`, `docs/ROADMAP.md` checkboxes.

**Acceptance criteria.**
- Every published number has corpus `sha256`, exact command, version and host profile.
- Every remaining estimate is labelled `estimate`.
- Docling and PaddleOCR each have an explicit verdict with the evidence that produced it.
- No `gi_*` flag is enabled by default in the committed configuration.

---

## 2. Dependency-gated milestones

M4 and M11 may require an **approved** new dependency. Until approval, their measurable part runs
against the incumbent, and the report states the incumbent result plus a `NOT MEASURED` for the
candidate. **No milestone installs a dependency on its own.**

---

## 3. Risk register for the plan

| Risk | Milestone | Control |
| --- | --- | --- |
| New block type breaks a consumer | M2–M6 | additive blocks; golden chunk tests; inverted guards |
| Table extraction changes a citation | M4 | `page_text` always retained; locator keys only added |
| Function-name/test coupling in refactors | M1 | existing tests must pass **unchanged** |
| Heavy dependency slips into the image | M4, M11 | dependency group gated on owner approval; `uv sync --frozen` |
| Corpus contradicts "no binaries" | M0 | generate fixtures at test time; assert absence of committed binaries |
| Cross-sheet work duplicates business SQL | M7 | read-only relation model; no LLM SQL |
| Silent behaviour change with flag off | all | flag-off golden tests are a hard gate |

---

## 4. Sequencing summary (proposed)

`M0 → M1 → M2 → M3 → M4 → M5 → M6 → M8 → M9 → M10 → M7 → M11`, with M5/M6/M8/M9/M10 parallelisable
after M2. Each milestone is a separate commit on `feature/global-intelligence-engine`; `main` is
never touched.

**Awaiting owner approval before any milestone is started.**
