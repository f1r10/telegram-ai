# Phase 2 — Document Intelligence Audit

**Status:** investigation only. No application code was modified, no dependency was installed, no
schema changed, no `gi_*` flag was activated. Everything below is either quoted from the repository
or marked as an estimate.

**Date:** 2026-11-12 · **Branch:** `feature/global-intelligence-engine` (Phase 1 committed at
`81d208f`, `d86516c`).

## 0. Method

Files read in full or in the cited ranges:

| Area | Files |
| --- | --- |
| Parser adapters | [parsers.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/parsers.py) |
| Normalized model | [models.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/models.py) |
| OCR | [pdf_ocr.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/pdf_ocr.py), [image_ocr.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/image_ocr.py), [ocr_replay.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/ocr_replay.py) |
| Images | [pdf_images.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/pdf_images.py), [image_metadata.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/image_metadata.py), [vision.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/vision.py) |
| Persistence | [models.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/infrastructure/database/models.py) |
| Index | [chunking.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/knowledge/chunking.py), [indexing.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/knowledge/indexing.py) |
| Benchmarks | [evaluate_docling.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/scripts/evaluate_docling.py), [benchmark_ocr.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/scripts/benchmark_ocr.py) |
| Decisions | ADR [0006](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/architecture/decisions/0006-deterministic-document-parsers.md), [0007](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/architecture/decisions/0007-pypdf-license-maintenance.md), [0017](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/architecture/decisions/0017-vision-extraction-for-images.md) |
| Dependencies | [pyproject.toml](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/pyproject.toml), [Dockerfile](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/Dockerfile) |

The evaluation corpus counts in §2.9 were produced by running the shipped generator offline —
`build_reference_corpus().coverage()` — not by reading the source by eye.

## 1. Executive summary

The repository already has a **working, deterministic, provenance-preserving document pipeline**.
Phase 2 is therefore mostly *extension and measurement*, not construction. Concretely:

**Already real and reusable**

- A typed normalized model (`ContentBlock` / `EmbeddedImage` / `ParsedDocument`) that every adapter
  targets.
- A `DocumentParser` protocol plus a `ParserRegistry` that routes by extension — TXT, PDF, DOCX,
  XLSX, JPG/JPEG/PNG.
- Deterministic per-format provenance locators that survive parser → chunk → Qdrant → citation:
  `{"page": n}` for PDF, `{"paragraph_index": n}` / `{"table_index": n, "row": n}` for DOCX,
  `{"sheet": name, "range": "A1:P4"}` for XLSX.
- A bounded OCR path (Tesseract + `pdftoppm`), scanned-page detection, a deferred-OCR replay job with
  backoff, and hard resource caps on every axis.
- Embedded-image extraction from PDF/DOCX/XLSX with per-picture locators and an 8 MiB / 200-image cap.
- A documented, sourced open-source integration matrix that already decided Docling and PaddleOCR are
  **evaluate-in-Phase-2**, not adopted.

**Missing for Phase 2**

- **PDF table structure** — none. All native PDF text becomes one `page_text` block per page; no
  rows, columns, or cell coordinates. Table recovery is the single largest functional gap.
- **Measured accuracy** — zero. No parser comparison, no per-language OCR accuracy, no latency or
  memory numbers exist anywhere in the repo. `docs/PHASE12B-BENCHMARK.md` states this explicitly.
- **No OCR engine abstraction** — Tesseract is invoked directly by two services; swapping engines is a
  code change, not a configuration change.
- **No confidence scores** — extracted text carries no confidence, so a low-quality OCR page is
  indistinguishable from a clean one.
- **Excel cached values** — `load_workbook(..., data_only=False)` reads formula *strings*, never the
  cached result, so "what is the computed stock" cannot be answered.
- **No cross-sheet / cross-document reasoning** — every file is processed in isolation.
- **DOCX gaps** — no lists, no numbering, no document metadata, no explicit table cell coordinates
  (cells are a joined string plus a row index).
- **No document-level structure** — no reading order, no header/footer stripping, no page-number
  detection for the body stream.

## 2. What already exists

### 2.1 Normalized document model

[processing/models.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/models.py)

- `ContentBlock(sequence, block_type, text, locator, metadata)` — lines 7–13.
- `EmbeddedImage(locator, data, content_type)` — lines 16–26.
- `ParsedDocument(source_file_id, parser_name, parser_version, media_type, metadata, blocks,
  warnings, requires_vision, requires_ocr, embedded_images)` — lines 29–40.
- `as_dict()` deliberately strips raw image bytes and reports `byte_size` only (lines 42–52), so the
  derived JSON never contains a picture.
- `text()` joins block text with a blank line (lines 54–55).

### 2.2 Parser adapters

[processing/parsers.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/parsers.py)

| Adapter | Lines | Library | Block types produced | Locator |
| --- | --- | --- | --- | --- |
| `TxtParser` | 115–134 | none (UTF-8-sig) | `plain_text` | `{line_start, line_end}` |
| `PdfParser` | 137–310 | `pypdf` | `page_text`, `page_ocr` | `{page}`, plus `{extraction: tesseract}` for OCR pages |
| `DocxParser` | 415–575 | `python-docx` | `heading`, `paragraph`, `table_row` | `{paragraph_index}` / `{table_index, row}` |
| `XlsxParser` | 578–723 | `openpyxl` | `table_row` | `{sheet, range}` |
| `ImageProbeParser` | 726–774 | none + Tesseract | `image_ocr` | `{image_index, extraction}` |

- `DocumentParser` protocol — lines 105–112 (requires `name`, `version`, `embedded_origin`, and
  `parse(path, *, source_file_id, media_type)`).
- `ParserRegistry` — lines 785–814. Routes `.txt/.pdf/.docx/.xlsx/.jpg/.jpeg/.png`; raises
  `ValueError("No parser registered for <suffix>")` otherwise.

Notable existing behaviour worth preserving:

- **PDF page-level isolation.** A page that fails native extraction appends a warning and continues
  (lines 151–156); the document is never discarded for one bad page.
- **Scanned-page detection is implicit but real.** `text == ""` after `page.extract_text()` ⇒ the page
  is treated as image-only (lines 157–186). `requires_ocr = bool(text_warnings)` (line 205).
- **OCR is opt-in by injection.** `PdfParser(ocr_service=...)` (line 142) — a `None` service leaves
  the "no extractable text; OCR may be required" warning in place.
- **XLSX keeps physical column positions**, including empty cells between populated ones, with the
  comment "Dropping them can silently attach an amount to the wrong field" (lines 691–693). It also
  emits an explicit warning when a numeric column has a blank first-row heading (lines 719–723).
- **`data_only=False`** (line 584) ⇒ formula strings are preserved verbatim, never silently replaced
  by computed values.

### 2.3 OCR services

| Service | File | Engine | Details |
| --- | --- | --- | --- |
| `PdfOcrService` / `PdfOcrSession` | [pdf_ocr.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/pdf_ocr.py) | `pdftoppm` → `tesseract` | `--psm 3`; languages `aze+eng+rus`; max 80 pages; 15 s/page; 150 s/document; 16 MiB raster; 1 MiB text |
| `ImageOcrService` | [image_ocr.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/image_ocr.py) | `tesseract` | `--psm 6`; ≤4 MiB; ≤8192 px side; ≤24 MP; 15 s |
| `OcrReplayService` | [ocr_replay.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/processing/ocr_replay.py) | re-runs parsing | `OcrReplayJob` table, exponential backoff, max attempts |

Both engines are invoked as **argument vectors** (never a shell), with `stdout` captured to a file and
output size capped. The default language set is validated against `[a-z]{3}(?:\+[a-z]{3})*\Z`
(`pdf_ocr.py:14`), and the Dockerfile installs `tesseract-ocr-aze`, `-eng`, `-rus` plus
`poppler-utils` ([Dockerfile](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/Dockerfile#L12-L16)).

### 2.4 Embedded-image extraction

- `_ExtractedImages` (parsers.py:72–102) enforces `MAX_EMBEDDED_IMAGE_BYTES = 8 MiB` (line 40) and
  `MAX_EMBEDDED_IMAGES_PER_DOCUMENT = 200` (line 41), and records a warning for every payload it
  refuses.
- PDF: only XObjects the content stream actually **draws** are collected, in draw order
  (lines 209–247); inline images are counted and warned about, not stored (lines 223–227); one Form
  level deep is traversed (lines 249–278). Locator: `{page, image_index, xobject}` (+ `form_xobject`).
- DOCX: body, headers and footers, with merged cells read from XML rather than `table.rows[i].cells[j]`
  so one picture cannot claim several grid positions (lines 505–575).
- XLSX: drawing parts read directly from the package, because openpyxl returns no pictures without
  Pillow (lines 605–658). Locator adds `anchor_cell`.
- `ImageAssetOrigin` distinguishes `PDF_EMBEDDED` / `DOCX_EMBEDDED` / `XLSX_EMBEDDED`
  (`embedded_origin` on each adapter).

### 2.5 Persistence and job pipeline

[infrastructure/database/models.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/infrastructure/database/models.py)

| Model | Line | Role |
| --- | --- | --- |
| `FileObject` | 200 | raw committed blob, sha256, content type |
| `IngestionSession` / `IngestionSessionItem` | 221 / 254 | `/saxla` staging |
| `IngestionJob` | 297 | leased worker job |
| `DocumentParseRun` | 333 | one per file: `parser_name`, `parser_version`, `normalized_storage_key`, `text_storage_key`, `requires_ocr`, `requires_vision`, `warning_count`, `error_message` |
| `OcrReplayJob` | 371 | deferred OCR retry |
| `KnowledgeIndexJob` / `KnowledgeIndexRecord` | 437 / 474 | vectorisation state |
| `ImageAsset` / `ImageAssetOccurrence` | 1292 / 1325 | deduplicated pictures + where they occur |

`DocumentProcessingStatus` (line 169) is the key visibility contract: `SUCCEEDED` / `DEFERRED` /
`FAILED` — an image-only document is `DEFERRED`, never silently `SUCCEEDED` with no text.

### 2.6 Chunking, indexing, retrieval

[knowledge/chunking.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/knowledge/chunking.py)

- Blocks are accumulated to `config.max_chars` with `overlap_chars`; an oversized single block is
  split with overlap (lines 72–83).
- A `heading` block sets `heading_context`, which is prepended to subsequent retrieval text (lines
  70–71, 109).
- **Provenance is aggregated, not flattened**: `locator = {"blocks": [block.locator, ...]}`
  (line 40).
- `stable_chunk_id()` is a SHA-256 over `(source_file_id, parse_run_id, chunking_version, sequence,
  locator)` (lines 93–106) — re-indexing the same artifact yields identical ids.
- `ChunkingConfig(version, max_chars, overlap_chars)` is supplied from settings
  (`settings.chunking_version`, `chunk_max_chars`, `chunk_overlap_chars`) in
  [workers/ingestion.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/workers/ingestion.py#L134-L137).

### 2.7 Business-specific spreadsheet importers (do not confuse with Phase 2)

- [application/inventory_xlsx.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/application/inventory_xlsx.py) — strict single-sheet inventory import; **rejects any formula**.
- [application/sales_import.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/application/sales_import.py) — strict single-sheet sales import; **rejects any formula**.

These are narrow, schema-validated, human-confirmed business intakes. They are **not** general
document intelligence and Phase 2 must not weaken or repurpose them.

### 2.8 Existing benchmark assets

| Asset | Measures | Corpus | Recorded result |
| --- | --- | --- | --- |
| [evaluate_docling.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/scripts/evaluate_docling.py) | phrase recall, native vs Docling, on a private PDF + private truth JSON | owner-private | **none** |
| [benchmark_ocr.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/scripts/benchmark_ocr.py) | code precision/recall/F1, exact-set accuracy, hit rate, false-alarm rate, per scenario | owner-private JSONL | **none** |
| [run_local_ocr.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/scripts/run_local_ocr.py) | produces OCR predictions with local Tesseract | owner-private | **none** |
| [diagnose_image_ocr.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/scripts/diagnose_image_ocr.py) | single-image OCR/index/retrieval probe | one file | n/a |

`evaluate_docling.py` is **strictly offline by design**: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`,
`do_ocr=False`, `do_table_structure=True`, `enable_remote_services=False`,
`allow_external_plugins=False`, local model artifacts required, symlink/magic-byte rejection, 8 MiB
Markdown cap ([evaluate_docling.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/src/office_assistant/scripts/evaluate_docling.py#L61-L93)).

### 2.9 Evaluation corpus — measured coverage

`build_reference_corpus().coverage()` (run offline today):

```
{'pdf': 22, 'docx': 6, 'xlsx': 5, 'txt': 7, 'images': 10, 'scanned_documents': 5,
 'contracts_and_invoices': 7, 'languages': 4, 'duplicate_groups': 1,
 'contradiction_groups': 2, 'unrelated_documents': 5, 'injection_documents': 1}
40 documents, 10 images
```

The corpus is **code-generated text, not binary files**
([corpus.py](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/tests/evaluation/corpus.py#L1-L12)).
Each `CorpusDocument` carries the text a pipeline *would* extract; each `CorpusImage` carries
`ocr_ground_truth` (lines 44–58). **There are no real PDF/DOCX/XLSX bytes and no real image pixels in
the repository today.** That is the single biggest constraint on Phase 2 benchmarking.

## 3. Capability matrix vs the Phase 2 target

Legend: **YES** = present and usable · **PARTIAL** = present with a real limitation · **NO** = absent.

| Requirement | Status | Evidence / limitation |
| --- | --- | --- |
| PDF native text extraction | YES | `pypdf` `page.extract_text()`, parsers.py:153 |
| PDF scanned-page detection | PARTIAL | only "text empty ⇒ OCR"; a *low-quality* text layer is not detected (parsers.py:157–186) |
| PDF OCR fallback | YES | `PdfOcrService` via `pdftoppm`+Tesseract, capped |
| PDF table extraction | **NO** | whole page is one text block; no rows/columns/cells (parsers.py:158–165) |
| PDF layout / reading order | **NO** | `extract_text()` output order only; no bbox, no column model |
| Headers/footers/page numbers | **NO** | not distinguished from body text |
| Excel multiple worksheets | YES | `for sheet in workbook.worksheets`, parsers.py:588 |
| Excel cross-sheet relationships | **NO** | no cross-sheet joins anywhere |
| Excel formulas | PARTIAL | formula **strings** kept, cached values never read (`data_only=False`, parsers.py:584) |
| Excel table/merged-cell detection | **NO** | rows are emitted; ranges are computed from populated cells only |
| Excel cell coordinates + provenance | YES | `{sheet, range}` + `values/formulas/columns` metadata (parsers.py:712–716) |
| Word headings/hierarchy | PARTIAL | style-name prefix `heading` only; no level, no outline tree (parsers.py:437) |
| Word paragraphs/tables | YES | `paragraph`, `table_row` (parsers.py:438–464) |
| Word lists | **NO** | lists are ordinary paragraphs |
| Word document metadata | **NO** | `metadata={}` (parsers.py:470) |
| Word embedded images (+ OCR) | PARTIAL | extraction YES; OCR of embedded pictures in DOCX is **not** wired |
| Multilingual OCR (az/en/ru/tr) | PARTIAL | `aze+eng+rus` configured; **tr** and accuracy unmeasured |
| Preserve original text | YES | `source_text` kept alongside `text` in `DocumentChunk` |
| Confidence scoring | **NO** | no confidence anywhere |
| Provenance to file/page/sheet/cell/paragraph | YES | locators above, aggregated per chunk |
| Visible partial-failure status | YES | `warnings`, `warning_count`, `requires_ocr`, `DEFERRED` |

## 4. Architectural gaps

| # | Gap | Consequence |
| --- | --- | --- |
| G1 | No structured table model for PDF | Scenario D and any "what does this contract table say" question cannot be answered from evidence |
| G2 | No parser/OCR **provider interface** — Tesseract is called directly | PaddleOCR/RapidOCR cannot be benchmarked or adopted without editing production services |
| G3 | No confidence score on extracted text | A 40 %-confidence OCR line is cited exactly like a clean digital line; §13's "per-statement verification" has nothing to verify against |
| G4 | No benchmark without a private corpus | Docling/PaddleOCR decisions are blocked on data that does not exist in the repo |
| G5 | `data_only=False` only | computed values (totals, lookups) are unavailable; a formula-aware read is a *second* pass |
| G6 | No cross-sheet reasoning | Scenario D's "code on sheet 1, quantity on sheet 2" is unanswerable |
| G7 | No reading-order / layout model | multi-column PDFs interleave text silently — a correctness bug, not just a quality gap |
| G8 | No headers/footers suppression | every page's footer text is indexed as content, inflating retrieval noise |
| G9 | No DOCX list or outline structure | numbering and nesting are lost; headings have no level |
| G10 | Embedded images are stored but not text-extracted | a scanned diagram inside a DOCX/PDF is invisible to search |
| G11 | No resource isolation for parsing | a hostile file runs in the worker process; caps exist but there is no per-document memory ceiling or timeout at the parse level |
| G12 | No document-level normalization layer | adapters each invent their own block vocabulary; there is no shared "document structure" object Phase 3–5 could consume |

## 5. Integration points

Phase 2 should plug into the following seams — all of them already exist:

1. **`DocumentParser` protocol** (parsers.py:105–112) — new parsers (e.g. a Docling adapter) implement
   this and register with `ParserRegistry`; no caller changes.
2. **`ParserRegistry`** (parsers.py:785–814) — extension-based routing is where an opt-in
   `docling` route is added behind a flag.
3. **`ParsedDocument`** (processing/models.py:29–40) — the shared output; new structured blocks must
   travel as `ContentBlock` with a richer `locator`/`metadata`, not as a parallel type.
4. **`DocumentParseRun`** (database/models.py:333) — `parser_name`/`parser_version` already record which
   engine produced a run, so two engines can coexist and be compared.
5. **`chunk_normalized_document`** (chunking.py:21) — consumes `{"blocks": [...]}`; a richer table
   block is picked up for free if it uses `text` + `locator`.
6. **`evaluate_docling.py`** — already the sanctioned measurement harness; extend rather than replace.
7. **Phase 1 planner flags** (`global_intelligence/flags.py`) — a `document_intelligence_enabled`
   stage flag is where a Phase 2 capability is switched on, defaulting to `False`.

The Phase-1 package already reserves the Phase-2 file names in
[IMPLEMENTATION_PLAN.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/architecture/IMPLEMENTATION_PLAN.md#L90-L93):
`global_intelligence/providers/document_intelligence.py`, `.../ocr.py`, and
`global_intelligence/adapters/`.

## 6. Invariants Phase 2 must not break

- `POSTGRESQL` is the system of record; Qdrant stays a rebuildable projection.
- Raw blobs are never modified; derived artifacts are written atomically.
- Parsed/retrieved text and images are **untrusted data**, never instructions (ADR 0017 §6).
- Partial or failed processing must be visible (§6): `DEFERRED` + `warnings`, never a silent success.
- Numbers come from PostgreSQL or deterministic computation, never from the LLM.
- Every extracted fact keeps a locator (file, page, sheet, cell, paragraph or table).
- Existing `/saxla`, inventory and sales XLSX intakes keep their strict behaviour.

## 7. Risks

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| R1 | Docling's model download and CPU cost violate the deployment envelope | High | benchmark before adoption; keep it behind an adapter and a flag; `evaluate_docling.py` already enforces offline mode |
| R2 | A heavier OCR engine conflicts with the pinned `uv.lock` | High | separate optional dependency group; never in the runtime image until measured |
| R3 | Per-language accuracy claims turn out to be estimates | High | only publish numbers the private corpus can actually produce; AZ in particular is unmeasured (ADR 0017 calls it weak) |
| R4 | A new block type breaks existing chunk/index consumers | Medium | new blocks are additive `ContentBlock`s; golden chunk tests |
| R5 | Table extraction regressions change existing citations | Medium | F1-style inverted regression guards, as used in Phase 1 |
| R6 | Cross-sheet reasoning duplicates `business_tools` SQL | Medium | read-only, deterministic; no free-form LLM SQL (§11) |
| R7 | Requiring a real binary corpus contradicts "never commit binaries" | Medium | generate binaries deterministically at test time into a temp dir |

## 8. Open questions for the owner

1. **Corpus.** May Phase 2 generate small *binary* fixtures (PDF/DOCX/XLSX/PNG) deterministically at
   test time from the existing synthetic text corpus, keeping the repo binary-free? Without this,
   every parser number stays "NOT MEASURED".
2. **Docling.** Is a ~500 MB model download and CPU-only inference (hundreds of MB RAM per document)
   acceptable for the target host *at all*, or is it benchmark-only with no adoption path?
3. **Azerbaijani OCR.** Is a measurable AZ OCR target (e.g. ≥ X % character accuracy on a private
   labelled set) required before any OCR engine change, or is AZ best-effort acceptable?
4. **Cross-sheet scope.** Is Phase 2 expected to answer cross-sheet *questions* (Phase 5 territory) or
   only to *represent* cross-sheet structure with provenance (Phase 2/3)?
