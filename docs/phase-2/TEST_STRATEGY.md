# Phase 2 — Test Strategy

**Status:** planning only. No test was written or run for Phase 2; nothing was installed. This
document separates **what can be tested offline today** from **what needs a real corpus or an owner
decision**, and it labels every expected number as *measured* or *estimate*.

**Companion documents:** [IMPLEMENTATION_PLAN.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/IMPLEMENTATION_PLAN.md) ·
[ARCHITECTURE.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/ARCHITECTURE.md) ·
[PARSER_BENCHMARK.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/PARSER_BENCHMARK.md)

**Date:** 2026-11-12 · **Branch:** `feature/global-intelligence-engine`

---

## 1. Principles

1. **`make test` must pass offline** — no live internet, no real LLM, no real Telegram (AGENTS.md).
   Every Phase-2 test obeys this: OCR is exercised through a **stub provider** in unit tests; a real
   Tesseract call is only in explicitly marked tests.
2. **Real evidence only.** A capability is "verified" only with a captured command + output.
   Everything else is `NOT MEASURED` or `estimate` (owner constraint; audit §0).
3. **Golden tests are the compatibility contract.** For every milestone, a test asserts that with the
   flag **off**, output is byte-for-byte identical to pre-Phase-2.
4. **Inverted regression guards.** For each new capability, a test asserts the *absence* of the new
   behaviour when it should not fire (used successfully in Phase 1).
5. **Fixtures are generated, never committed.** Real binaries are written at test time into a temp
   directory from the synthetic corpus (audit risk **R7**).

---

## 2. Test layers

| Layer | Scope | Runs offline | Where |
| --- | --- | --- | --- |
| L1 Unit | one parser/provider/function, stubbed I/O | yes | `tests/processing/`, `tests/` |
| L2 Consumer | `ParsedDocument` → chunk → locator integrity | yes | `tests/knowledge/` |
| L3 Integration | full ingestion → derived artifacts → index (needs PostgreSQL) | **marked `integration`** | `tests/integration/` |
| L4 Benchmark | accuracy/latency/memory on real bytes | **manual, registered** | `scripts/` + `docs/evaluation/` |

Existing coverage that must keep passing (from the Phase-2 audit) is the regression baseline. The
new Phase-2 tests slot in beside it; **no existing test may be weakened**.

---

## 3. Representative evaluation corpus

### 3.1 What exists

`build_reference_corpus()` — **40 documents, 10 images**, measured offline today:

```
{'pdf': 22, 'docx': 6, 'xlsx': 5, 'txt': 7, 'images': 10, 'scanned_documents': 5,
 'contracts_and_invoices': 7, 'languages': 4, 'duplicate_groups': 1,
 'contradiction_groups': 2, 'unrelated_documents': 5, 'injection_documents': 1}
```

It is **text-only** ([corpus.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/tests/evaluation/corpus.py#L1-L12)):
each `CorpusDocument` carries the text a pipeline *would* extract; each `CorpusImage` carries
`ocr_ground_truth`. There are no real bytes and no real pixels.

### 3.2 What Phase 2 must add

| Fixture (generated at test time) | Built from | Purpose |
| --- | --- | --- |
| `simple.pdf` | synthetic text | native text baseline |
| `two_column_contract.pdf` | two text columns | reading-order truth (M3) |
| `ruled_table.pdf` | a drawn grid + cells | table cell truth (M4) |
| `scanned_page.pdf` | an image-only page (rendered text) | OCR fallback + `requires_ocr` |
| `furniture.pdf` | N pages + repeating footer | header/footer detection (M3) |
| `multi_sheet.xlsx` | 2 sheets, shared key, a formula | cached values (M5) + relation (M7) |
| `headings.docx` | 3 heading levels + a numbered list | DOCX structure (M6) |
| `embedded_image.docx` | a picture containing text | embedded-image OCR (M10) |
| `az.png` / `ru.png` / `en.png` / `tr.png` | text rendered in each language | multilingual OCR (§5) |
| `corrupt.pdf` / `truncated.docx` / `bad.xlsx` | truncated/invalid bytes | corrupt-file handling (§7) |

**Constraint:** these are generated into a temp dir and asserted **not** to be committed (a test
checks the repo tree for stray binaries). Real Azerbaijani/Russian/Turkish **recognition accuracy**
still needs an owner-supplied labelled set — generated text renders cleanly and is not a substitute
for phone-photo reality (ADR 0017 calls AZ quality "weak").

### 3.3 Honest split

| Question | Answerable with generated fixtures | Needs owner private set |
| --- | --- | --- |
| Does extraction find the text? | YES | — |
| Is reading order correct? | YES | — |
| Is a table's cell grid recovered? | YES | — |
| Does `requires_ocr` fire correctly? | YES | — |
| Latency / memory per format | YES (host-specific) | — |
| **Per-language OCR character accuracy** | *partly* (clean rendering only) | **YES** for real accuracy |
| **Docling vs incumbent on real contracts** | NO | **YES** |

---

## 4. Required test suites

### 4.1 Unit tests (L1)

- **Parser adapters** — each produces the expected block types and locators for its fixture;
  `locator` is present and non-empty on **every** block (a parametrised assertion over all parsers).
- **`OCRProvider`** — a stub provider returns fixed text/confidence; the service produces identical
  blocks to the pre-refactor behaviour (M1 golden test).
- **Normalization** — `DocumentStructure` is deterministic (same input twice ⇒ identical JSON) and
  does not alter `blocks[]`.
- **Confidence** — present for OCR, absent (`not 1.0`) for native text.
- **Flags** — with `gi_document_intelligence_enabled=False`, every new code path is inert.

### 4.2 Integration tests (L3, `integration` marker)

- Full ingestion of a generated fixture → `DocumentParseRun` reaches the expected status →
  derived `normalized.json` + `text.txt` written → chunk → index record created.
- `DocumentParseRun.parser_name`/`parser_version` correctly record the engine that ran.
- A flag-off run and a flag-on run produce the **same** `chunk_id`s for a document with no new
  features (compatibility proof).

### 4.3 Multilingual OCR tests

- Per language (`az`, `en`, `ru`, `tr`): a rendered image → provider → text.
- **Assert the configured language set matches the provider's `supported_languages`.**
  Today the image has `{aze, eng, rus}`; requesting `tur` must fail **loudly** (declared
  unsupported), never silently degrade.
- **Byte-identical preservation:** extracted text is stored exactly as returned; a test asserts no
  normalisation is applied to stored OCR text (owner requirement: never silently change extracted
  information).
- `psm` variants (`--psm 3` for PDF pages, `--psm 6` for images) are asserted unchanged.

### 4.4 PDF table extraction tests

- Ruled table → each cell's text recovered at the expected `(row, col)`.
- Borderless/aligned table → documented behaviour, with the metric (cell F1) reported, not assumed.
- Malformed/ragged table → warning, no crash.
- **Additive proof:** the `page_text` block is still emitted alongside the `table` block.

### 4.5 Excel cross-sheet reasoning tests

- Shared key column across two sheets → a relation edge with **both** endpoints resolving to real
  cells (`{sheet, range}`).
- No cue ⇒ **no** relation (negative test).
- Formula cell → both formula and cached value recorded, labelled.
- The strict intakes (`inventory_xlsx.py`, `sales_import.py`) still **reject** formulas — their
  existing tests pass unchanged.

### 4.6 Large-document / performance tests

- Large generated fixture → wall clock and peak RSS recorded and compared against the configured
  budget.
- A document beyond the budget → visible abort (M9), never a hang.
- Marked as **non-gating** on slow hosts, but the *abort* behaviour is asserted.

### 4.7 Corrupted-file handling

| Fixture | Expected |
| --- | --- |
| truncated PDF | warning + `FAILED`/`DEFERRED`, no crash |
| PDF with one bad page | other pages extracted; warning for the bad page (existing behaviour, must hold) |
| invalid OOXML zip | `FAILED` with a reason, no crash |
| wrong magic bytes vs extension | rejected before processing (existing vision rule, ADR 0017 §2) |
| empty file | explicit "no text" status, not a silent success |
| zip-bomb-style XLSX | abort at the resource ceiling |

### 4.8 Regression tests for existing functionality

- Golden flag-off artifacts for PDF/DOCX/XLSX/TXT.
- `stable_chunk_id()` golden values (locator keys only ever **added**).
- `DocumentChunk.text` unchanged by confidence.
- The 71 Phase-1 GI/evaluation tests still pass.
- The `/saxla`, inventory and sales flows unchanged.

---

## 5. Metrics and thresholds

Only structural correctness is assertable today. **No accuracy threshold is asserted in Phase 2**
until a measured basis exists (audit open question #3).

| Metric | Asserted in Phase 2? |
| --- | --- |
| locator presence on every block | **YES** — hard gate |
| determinism of `structure` | **YES** — hard gate |
| flag-off byte-identity | **YES** — hard gate |
| `requires_ocr` correctness | **YES** — hard gate |
| provider-language capability match | **YES** — hard gate |
| cell-level table F1 ≥ X | **NO** — report only |
| per-language OCR accuracy ≥ X | **NO** — report only |
| latency / RSS ≤ X | **NO** — report only |

---

## 6. Regression-guard technique (as used in Phase 1)

For every new capability, add a test that **fails if the capability fires when it should not**:

- table block ⇒ assert absent for a PDF with no table;
- `page_furniture` ⇒ assert absent when no line repeats;
- confidence ⇒ assert absent for native text;
- cached value ⇒ assert absent when the workbook has no formulas;
- relation ⇒ assert absent when sheets share no key.

This is the inverted guard that caught real defects in Phase 1 and is the cheapest protection against
a flag leaking into default behaviour.

---

## 7. Entry / exit criteria per milestone

**Entry:** the milestone's predecessor is green (`make test lint typecheck`); its flag exists and
defaults `False`.

**Exit (all required):**
1. New tests pass **and** the full suite passes offline.
2. Flag-off golden test proves byte-identity.
3. Inverted guard added.
4. Any measured number is registered with corpus `sha256`, command, version, host profile.
5. Any remaining uncertainty is written as `NOT MEASURED` + reason, with an `OPEN-QUESTIONS.md` entry.
6. Lint and typecheck clean; no TODO without an open-question entry.

---

## 8. Test-data handling and privacy

- Private labelled sets are **never** committed and **never** echoed into output
  (the existing `evaluate_docling.py` `truth_sha256` discipline is the template).
- Generated fixtures live in a temp dir and are removed after the run.
- OCR text is treated as **untrusted data** in tests too — a fixture whose text looks like an
  instruction must be asserted to remain inert (prompt-injection regression).
- Documents contain no real customer data; the synthetic corpus is the only source.

---

## 9. Honest status

| Suite | Status today |
| --- | --- |
| Phase-1 regression suite | **passing** (71 collected: 70 passed + 1 xfailed) |
| Phase-2 unit/integration tests | **not written** — this document is the plan |
| Parser / OCR benchmarks | **not run** — no corpus (PARSER_BENCHMARK §0) |
| Multilingual accuracy | **not measured** — no labels |

No Phase-2 capability is claimed verified by this document.
