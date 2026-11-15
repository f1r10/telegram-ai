# Phase 2 — Document Intelligence Architecture

**Status:** design proposal. No application code was modified, no dependency installed, no schema
changed, no `gi_*` flag activated. Every integration point named here **already exists** in the
repository; the new names are proposals, marked as such.

**Companion documents:** [DOCUMENT_INTELLIGENCE_AUDIT.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/DOCUMENT_INTELLIGENCE_AUDIT.md) ·
[PARSER_BENCHMARK.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/PARSER_BENCHMARK.md) ·
[IMPLEMENTATION_PLAN.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/IMPLEMENTATION_PLAN.md)

**Date:** 2026-11-12 · **Branch:** `feature/global-intelligence-engine`

---

## 1. Design goals and non-goals

### Goals

1. **Add structure, not a second pipeline.** Every improvement travels through the existing
   `DocumentParser → ParsedDocument → DocumentParseRun → chunk → Qdrant` path.
2. **One seam per replaceable engine.** A parser and an OCR engine are behind interfaces, so they can
   be benchmarked, swapped and feature-flagged without editing callers (audit gap **G2**).
3. **Every extracted fact keeps a locator.** File, page, sheet, cell, paragraph, table, or image —
   always. This is already true (audit §2.2) and must stay true for new block types.
4. **Original text is never silently altered.** Extraction may *add* structure and *add* a
   confidence value; it must not rewrite, normalise away, or substitute extracted content. Where a
   computed value exists (an Excel cached value), it is stored **beside** the formula, labelled.
5. **Degrade visibly.** A partial or failed extraction produces a `warning`, a `warning_count` and,
   when nothing usable was extracted, `DEFERRED` — never a silent success (invariant §6).
6. **Deterministic first.** Structure, ordering, geometry and provenance are computed by code, never
   by a model. A model may only be an *optional* accuracy upgrade behind a flag with a fallback.

### Non-goals

- Not a new document store, not a new index, not a new job system. PostgreSQL stays the system of
  record; Qdrant stays a rebuildable projection.
- Not an OCR-quality research project. Engine changes are gated on a measured corpus
  ([PARSER_BENCHMARK.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/PARSER_BENCHMARK.md) §5).
- Not a replacement for the strict, human-confirmed business intakes
  (`application/inventory_xlsx.py`, `application/sales_import.py`) — those keep their behaviour
  exactly (audit §2.7).
- Not cross-sheet *question answering* (Phase 5 territory); Phase 2 *represents* cross-sheet
  structure with provenance.

---

## 2. Where Phase 2 plugs in (existing seams only)

```
                       ┌──────────────────────────── existing ───────────────────────────┐
  upload ─► IngestionJob ─► workers/ingestion.py ─► ParserRegistry ─► DocumentParser
                                                   │                        │
                                                   │                        ▼
                                                   │                 ParsedDocument
                                                   │                 (blocks + locators)
                                                   │                        │
                                                   ▼                        ▼
                                          DocumentParseRun ◄──── derived/<file>/<run>/
                                          (parser_name,                   normalized.json
                                           parser_version,                text.txt
                                           requires_ocr, warnings)
                                                   │
                                                   ▼
                              chunk_normalized_document ─► DocumentChunk ─► Qdrant
                              (locator = {"blocks": [...]})     (source_text kept)
```

Phase 2 does **not** move any of these boxes. It changes what a `DocumentParser` returns (richer
`ContentBlock`s) and adds one new interface behind `ParserRegistry`.

| Seam | File | How Phase 2 uses it |
| --- | --- | --- |
| `DocumentParser` protocol | [parsers.py#L105-L112](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/parsers.py#L105-L112) | new parsers implement it unchanged |
| `ParserRegistry` | [parsers.py#L785-L814](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/parsers.py#L785-L814) | extension routing; flag-gated engine selection |
| `ParsedDocument` | [models.py#L29-L40](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/models.py#L29-L40) | shared output type; new structure travels inside it |
| `DocumentParseRun` | [database/models.py#L333](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/infrastructure/database/models.py#L333) | `parser_name`/`parser_version` already let two engines coexist and be compared |
| `chunk_normalized_document` | [chunking.py#L21](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/knowledge/chunking.py#L21) | consumes `{"blocks": [...]}` — a richer block is picked up for free |
| `EngineFlags` | [flags.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/global_intelligence/flags.py) | one new stage flag, default `False` |

The Phase-1 plan already reserved the Phase-2 file names:
[IMPLEMENTATION_PLAN.md#L90-L93](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/architecture/IMPLEMENTATION_PLAN.md#L90-L93) —
`global_intelligence/providers/document_intelligence.py`, `.../ocr.py`, `global_intelligence/adapters/`.

---

## 3. Target layered architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 7  Isolation & resource limits        (process/thread bound, caps, timeouts) │
├──────────────────────────────────────────────────────────────────────────────┤
│ 6  Error handling & fallback ladder   (native → structural → OCR → DEFERRED) │
├──────────────────────────────────────────────────────────────────────────────┤
│ 5  Confidence scoring                 (per block, optional, additive)        │
├──────────────────────────────────────────────────────────────────────────────┤
│ 4  Provenance model                   (locator on every block; aggregated)   │
├──────────────────────────────────────────────────────────────────────────────┤
│ 3  Document normalization / IR        (DocumentStructure over ContentBlocks) │
├──────────────────────────────────────────────────────────────────────────────┤
│ 2  OCR engine adapters                (OCRProvider; Tesseract today)         │
├──────────────────────────────────────────────────────────────────────────────┤
│ 1  Parser adapters                    (DocumentParser; per-format)           │
└──────────────────────────────────────────────────────────────────────────────┘
             all of it behind gi_engine_enabled + gi_document_intelligence_enabled
```

Layers 1, 4 and part of 6 exist today. Layers 2, 3, 5 and 7 are new. Nothing above layer 3 is
allowed to change what layer 3 emits.

---

## 4. Layer 1 — Parser adapters

**Unchanged interface.** A parser keeps this exact contract (parsers.py:105–112):

```python
class DocumentParser(Protocol):
    name: str
    version: str
    embedded_origin: ImageAssetOrigin | None
    def parse(self, path: Path, *, source_file_id: int, media_type: str) -> ParsedDocument: ...
```

Phase 2 adds parsers and enriches output; it does not change the protocol.

### 4.1 PDF — native text (keep), structure (add)

Today a page becomes **one** `page_text` block (audit §2.2). Phase 2 introduces a **layout pass** the
adapter may use, producing additional blocks *without deleting the page block*:

- `table` block — text = a deterministic rendered grid; `locator = {"page": n, "table_index": k}`;
  `metadata = {"rows": R, "cols": C, "cells": [{"row","col","text"}], "bbox": [...]}`.
- Preserved page anchor — the existing `page_text` block stays so ordering and compatibility are
  unchanged; the table block is **additive**.
- Reading order — block `sequence` follows a deterministic geometric order (top-to-bottom,
  left-to-right within a detected column band) rather than raw content-stream order. This closes
  audit gap **G7**, which is a correctness bug, not a quality gap.

### 4.2 PDF — headers, footers, page numbers (add)

Deterministic, not model-based: a line that repeats in the top/bottom band of ≥ N pages, or matches a
page-number pattern, is emitted as **its own block** with `block_type="page_furniture"` and
`metadata={"repeated_on": [...]}`, and is excluded from the body's retrieval text. The decision to
exclude is recorded as a `warning`-free, explicit structural fact — never a silent deletion of text.

### 4.3 XLSX — two-pass, formula-aware (add)

Keep `data_only=False` as the primary read (formula strings preserved verbatim, parsers.py:584).
Add a **second** `data_only=True` pass and store the computed value **beside** the formula:

- `locator = {"sheet": name, "range": "A1:P4"}` (unchanged, parsers.py:712–716).
- `metadata.values` = cached values; `metadata.formulas` = formula strings; each numeric cell carries
  `{"value": ..., "formula": ...}` when both exist. Neither replaces the other (goal §1.4).
- Merged-cell detection and explicit table regions are recorded in `metadata` (**G1**-analogue for
  XLSX; currently absent, audit §2.2).
- Cross-sheet relationships are **derived** as a separate, deterministic structure (Layer 3), not by
  the adapter (gap **G6**).

### 4.4 DOCX — read what python-docx already exposes (add)

No new library: the document XML already carries the data (audit §2.2, benchmark §3.7).

- **Heading level** from `w:outlineLvl` / built-in style depth → `metadata={"level": n}` → an outline
  tree (**G9**).
- **Lists and numbering** from `w:numPr` → `block_type="list_item"` with `metadata={"list_id","level"}`.
- **Table cell coordinates** from `w:tc` grid spans → `metadata={"cells": [...]}` instead of a joined
  string (keeps `{table_index, row}` and **adds** `col`).
- **Document metadata** from `core_properties` → `ParsedDocument.metadata` (currently `{}`, **G9**).
- **Embedded-image OCR** for pictures inside DOCX/PDF, gated by the same `OCRProvider` (**G10**).

---

## 5. Layer 2 — OCR engine adapters (`OCRProvider`)

**This is the missing seam (gap G2).** Today `PdfOcrService` and `ImageOcrService` call `tesseract`
directly, so no alternative engine can be benchmarked or adopted without editing production code.

Proposed interface — lives at the reserved path
`global_intelligence/providers/ocr.py` (IMPLEMENTATION_PLAN.md:91):

```python
class OcrResult(NamedTuple):
    text: str
    confidence: float | None          # None ⇒ engine reports no confidence
    engine: str                       # e.g. "tesseract"
    version: str
    languages: tuple[str, ...]
    warnings: tuple[str, ...]

class OCRProvider(Protocol):
    name: str
    version: str
    supported_languages: frozenset[str]
    def recognize(self, image: bytes, *, languages: tuple[str, ...], psm: int) -> OcrResult: ...
```

Rules:

1. **Tesseract stays the only registered implementation in Phase 2.** The seam is added so that
   PaddleOCR / RapidOCR can be added as an alternative *registration*, not as an edit.
2. The existing argument-vector invocation, size caps, timeouts, output cap and language-regex
   validation move **behind** the interface unchanged in behaviour (audit §2.3).
3. **`languages` must be validated against what the provider actually supports.** Turkish is not in
   the deployed image today (benchmark §3.5); the interface makes that a declared capability rather
   than a silent failure.
4. **No cloud OCR provider.** Untrusted document bytes do not leave the process (ADR 0017 §2).
5. Engine identity/version is recorded on the run (`DocumentParseRun.parser_version` today) so an
   OCR A/B is comparable after the fact.

---

## 6. Layer 3 — Document normalization and the structured intermediate representation

Today each adapter invents its own block vocabulary; there is no shared "document structure" object
(gap **G12**). Phase 2 introduces a **normalization layer** that sits *between* parser output and
chunking, without changing either:

- **Input:** `ParsedDocument.blocks` (list of `ContentBlock`).
- **Output:** a `DocumentStructure` — a deterministic, JSON-serialisable view of the same blocks with
  hierarchy, regions and relations made explicit:

```
DocumentStructure
├── pages[]        : page number, size (if known), furniture blocks, reading order
├── sections[]     : heading hierarchy (level, title, block range)
├── tables[]       : id, locator, dimensions, cells (row/col/text), source page/sheet
├── lists[]        : items with level and marker
├── relations[]    : cross-sheet / cross-reference edges (source locator → target locator)
└── blocks[]       : the flat, ordered ContentBlock list (unchanged for chunking)
```

**Compatibility rule that makes this safe:** the flat `blocks[]` list remains the *only* thing
chunking consumes (chunking.py:21). `DocumentStructure` is written **alongside** it in the same
`normalized.json` under a new key (e.g. `"structure"`), so:

- existing consumers that read `document["blocks"]` are byte-for-byte unaffected;
- `stable_chunk_id()` (chunking.py:93–106) is unaffected because it hashes the *chunk locator*, not
  the document;
- a Phase-3+ consumer can opt into `structure`.

Normalization is **pure and deterministic** — same input bytes ⇒ same `structure`. No model.

---

## 7. Layer 4 — Provenance model

Already real and must be preserved (audit §2.2, invariant §6). Phase 2 **extends** the vocabulary; it
never removes or rewrites an existing locator.

| Level | Locator (existing) | Phase 2 addition |
| --- | --- | --- |
| File | `source_file_id` on `ParsedDocument`/`DocumentChunk` | unchanged |
| Page | `{"page": n}` | `+ {"table_index": k}` for table blocks |
| Paragraph | `{"paragraph_index": n}` | `+ {"level": n}` for headings |
| Table | `{"table_index": n, "row": r}` | `+ "col"`, `+ cell coordinates` |
| Sheet/cell | `{"sheet": s, "range": "A1:P4"}` | `+ per-cell {row,col}` and `+ relation edges` |
| Image | `{"page","image_index","xobject"}` / `{"sheet","image_index","relationship_id","anchor_cell"}` | unchanged |

Two rules:

1. **Locators are additive.** Fields may be added; existing keys keep their meaning, because
   `stable_chunk_id()` and citation rendering depend on them.
2. **Every new block type must carry a locator** that resolves to a byte/page/sheet/paragraph
   position in the original file. A block without a locator is a defect, not a block.

---

## 8. Layer 5 — Confidence scoring

Nothing today carries a confidence value (gap **G3**). Phase 2 adds confidence as an **optional,
additive** attribute; it never changes the text.

- **Source of the value:** the OCR engine's own per-word/page confidence when it reports one
  (Tesseract does); for native text, confidence is *not invented* — a deterministic digital text
  layer is recorded as `"extraction": "native"` with no numeric score rather than a fake `1.0`.
- **Where it lives:** `ContentBlock.metadata["confidence"]` and a document-level aggregate on the
  parse run's derived artifact. It is **not** added to `DocumentChunk.text`, so retrieval behaviour is
  unchanged.
- **How it is used:** a low-confidence block is *flagged*, and downstream (Phase 5 grounding) may
  require human confirmation before such text becomes a business fact — matching the existing
  "OCR text never becomes a business fact automatically" rule (ADR 0017 §5). Phase 2 only *records*
  the value; it does not act on it, so it cannot regress current behaviour.
- **No threshold is invented here.** A confidence threshold is an owner decision with a measured
  basis (audit open question #3).

---

## 9. Layer 6 — Error handling and fallback ladder

The current behaviour is already good and is the model for Phase 2: a page that fails native
extraction appends a warning and continues (parsers.py:151–156); the document is never discarded for
one bad page.

Phase 2 makes the ladder explicit and uniform:

```
1. native extraction            → blocks + locators                       SUCCEEDED
2. structural pass              → + tables / order / furniture             SUCCEEDED (additive)
3. OCR fallback (opt-in)        → + page_ocr blocks                       SUCCEEDED
4. nothing usable extracted     → warning + requires_ocr/vision            DEFERRED
5. provider/library error       → warning, error_message, no crash          FAILED (reason kept)
```

Rules:

- **Never a silent success.** If a step yields nothing, the run says so (invariant §6).
- **A library failure is caught at the adapter boundary** (as today: `except Exception` per page,
  parsers.py:153–156) and becomes a warning, not a document loss.
- **Fallback is one-directional.** A structure pass that fails degrades to the pre-Phase-2 output;
  it never deletes the native text.
- **Provider failure never crashes the bot** (AGENTS.md invariant 8).

---

## 10. Layer 7 — Resource limits and processing isolation

Today caps exist per service (80 pages, 15 s/page, 150 s/document, 16 MiB raster, 8 MiB / 200 images)
but there is no per-document ceiling and no isolation beyond the worker process (gap **G11**).

Phase 2 adds, at the parse boundary:

- **A wall-clock budget per document** (configurable), aborting the parse into a visible warning.
- **A memory ceiling** for the parse step, so a hostile file cannot exhaust the worker.
- **A CPU-bound execution boundary** — long extraction runs in a bounded, isolated unit (the existing
  subprocess pattern already used for `pdftoppm`/`tesseract` is the model), so a hang is killable.
- **All limits configurable via settings/environment**, defaulting to today's values so nothing
  changes until an operator opts in.

Isolation is a boundary, not a sandbox: it protects the worker's availability and preserves the
"untrusted file" posture; it is not a security sandbox and must not be described as one.

---

## 11. Feature flags and rollout

Phase 2 adds **one** stage flag to `EngineFlags`, defaulting `False`, following the existing pattern
([flags.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/global_intelligence/flags.py#L21-L34), all nine existing flags default `False`):

```
gi_engine_enabled              (master, unchanged, False)
gi_document_intelligence_enabled   ← NEW, False
```

`EngineFlags` is `extra="forbid"` and `frozen=True`, so adding a flag is an explicit, reviewable edit
to `flags.py` **and** `core/settings.py` — never an accident.

- Flag **off** ⇒ behaviour is byte-for-byte the pre-Phase-2 pipeline.
- Flag **on** ⇒ structural pass, `OCRProvider` seam, confidence recording, `DocumentStructure` are
  active.
- A **second, finer flag** (e.g. an engine-selection flag per format) may be added only if the
  benchmark shows a per-format switch is needed; not before.

No `gi_*` flag is activated during this investigation (constraint §8).

---

## 12. Gap → design mapping

| # | Gap | Closed by | Layer |
| --- | --- | --- | --- |
| G1 | No PDF table model | §4.1 `table` block + cells | 1/3 |
| G2 | No parser/OCR provider seam | §5 `OCRProvider`; flag-routed `ParserRegistry` | 2 |
| G3 | No confidence | §8 additive `metadata.confidence` | 5 |
| G4 | No benchmark without a corpus | [PARSER_BENCHMARK.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/PARSER_BENCHMARK.md) §5 + [TEST_STRATEGY.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/TEST_STRATEGY.md) §3 | — |
| G5 | `data_only=False` only | §4.3 second, labelled pass | 1 |
| G6 | No cross-sheet reasoning | §6 `relations[]` (represent, not answer) | 3 |
| G7 | No reading order | §4.1 deterministic geometric order | 1 |
| G8 | No header/footer handling | §4.2 `page_furniture` | 1 |
| G9 | No DOCX list/outline/metadata | §4.4 read `outlineLvl`/`numPr`/`core_properties` | 1 |
| G10 | Embedded images not text-extracted | §4.4 + §5 via `OCRProvider` | 1/2 |
| G11 | No parse-level resource ceiling | §10 budget + ceiling + isolation | 7 |
| G12 | No document-level structure | §6 `DocumentStructure` | 3 |

---

## 13. Backward compatibility

| Concern | Guarantee |
| --- | --- |
| Existing consumers of `normalized.json["blocks"]` | unchanged — new data goes in `["structure"]` |
| `stable_chunk_id()` | unchanged algorithm; locator keys are only ever *added* |
| `DocumentChunk.text` / `source_text` | unchanged — confidence never enters `text` |
| `/saxla`, inventory/sales XLSX intakes | untouched — separate, strict, human-confirmed paths |
| DB schema | **no migration in Phase 2's structural work** — new data rides in existing JSON columns and existing `metadata`; a migration is proposed only if a milestone proves one is unavoidable, and then it is a separate, reviewable change |
| Flag off | pre-Phase-2 behaviour exactly |
| Phase 1 | untouched — Phase 2 does not modify understanding/planner/evidence code |

---

## 14. Proposed file layout

**New (proposed names; the last three are pre-reserved by the Phase-1 plan):**

```
src/office_assistant/processing/structure.py            # Layer 3 DocumentStructure + normalization
src/office_assistant/processing/ocr_provider.py         # OR  the reserved path below
src/office_assistant/processing/confidence.py           # Layer 5
src/office_assistant/global_intelligence/providers/document_intelligence.py   # reserved
src/office_assistant/global_intelligence/providers/ocr.py                     # reserved
src/office_assistant/global_intelligence/adapters/                            # reserved
```

**Changed (all surgical, all additive):**

```
src/office_assistant/processing/parsers.py              # structure pass, table blocks, DOCX attrs, XLSX 2nd pass
src/office_assistant/processing/pdf_ocr.py              # route through OCRProvider, unchanged behaviour
src/office_assistant/processing/image_ocr.py            # route through OCRProvider, unchanged behaviour
src/office_assistant/global_intelligence/flags.py       # + 1 flag
src/office_assistant/core/settings.py                   # + 1 setting (+ limits)
src/office_assistant/knowledge/chunking.py              # only if a table block needs a text projection (opt-in)
Dockerfile                                              # + tesseract-ocr-tur ONLY if Turkish is approved
```

The exact per-milestone file list is in
[IMPLEMENTATION_PLAN.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/IMPLEMENTATION_PLAN.md).

---

## 15. Explicit non-decisions (require owner approval)

1. Adopting Docling (or any heavy engine) — blocked on cost + benchmark.
2. Adding a heavy OCR engine — blocked on a measured accuracy gain.
3. Adding Turkish to the image — a Dockerfile + language decision.
4. Any confidence *threshold* — a measured, owner-set policy; Phase 2 only records the value.
5. Any database migration — not proposed by this design.
