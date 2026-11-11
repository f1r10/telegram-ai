# Phase 2 — Dependency and Security Review

**Status:** investigation only. **Nothing was installed, added, upgraded or removed.** No lock file
was regenerated. Every recommendation below is a *proposal requiring owner approval* before any
change (owner constraint §8; audit §7 risk **R2**).

**Companion documents:** [PARSER_BENCHMARK.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/PARSER_BENCHMARK.md) ·
[ARCHITECTURE.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/phase-2/ARCHITECTURE.md) ·
[OPEN_SOURCE_INTEGRATION_MATRIX.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/architecture/OPEN_SOURCE_INTEGRATION_MATRIX.md)

**Date:** 2026-11-12 · **Branch:** `feature/global-intelligence-engine`

---

## 0. Current dependency inventory (verified from the repo)

Source: [pyproject.toml](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/pyproject.toml#L11-L32),
[Dockerfile](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/Dockerfile).

### 0.1 Python runtime (`dependencies`)

| Package | Constraint | Role |
| --- | --- | --- |
| `alembic` | `>=1.14,<2.0` | migrations |
| `pydantic-settings` | `>=2.7,<3.0` | configuration (pulls `pydantic` v2) |
| `python-telegram-bot` | `>=21.0,<23.0` | bot |
| `sqlalchemy[asyncio]` | `>=2.0.36,<2.1` | ORM |
| `asyncpg` | `>=0.30,<1.0` | PostgreSQL driver |
| **`python-docx`** | `>=1.1,<2.0` | DOCX parsing (**Phase 2 relevant**) |
| **`openpyxl`** | `>=3.1,<4.0` | XLSX parsing (**Phase 2 relevant**) |
| **`pypdf`** | `>=5.0,<7.0` | PDF parsing (**Phase 2 relevant**) |
| `qdrant-client` | `>=1.14,<2.0` | vector index |
| `fastembed` | `>=0.7,<1.0` | embeddings |
| `openai` | `>=3.8,<4.0` | LLM/vision vendor |
| `httpx` | `>=0.28,<1.0` | HTTP |
| `trafilatura` | `==2.3.0` | web extraction (exact pin) |

### 0.2 Dev (`dependency-groups.dev`)

`pyright>=1.1.390,<2.0` · `pytest>=8.3,<9.0` · `ruff>=0.9,<1.0`

### 0.3 System packages (Dockerfile, installed as root, image runs as non-root `appuser` uid 10001)

`poppler-utils` (provides `pdftoppm`) · `tesseract-ocr` · `tesseract-ocr-aze` ·
`tesseract-ocr-eng` · `tesseract-ocr-rus`

**Note:** the build uses `uv 0.12.3` and `uv sync --frozen` against a committed `uv.lock`. Any new
Python dependency therefore **regenerates the lock** and is a reviewable supply-chain event, which is
exactly the guardrail we want.

---

## 1. Phase-2 relevant licences (current stack)

| Component | Licence | Commercial use | Notes |
| --- | --- | --- | --- |
| `pypdf` | **BSD-3-Clause** | OK | replaced PyMuPDF to avoid AGPL — [ADR 0007](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/architecture/decisions/0007-pypdf-license-maintenance.md) |
| `python-docx` | **MIT** | OK | |
| `openpyxl` | **MIT** | OK | |
| Tesseract | **Apache-2.0** | OK | invoked as a subprocess |
| `poppler-utils` | **GPL-2.0+** | OK as a separate process | not linked; `pdftoppm` is a subprocess (no derivation) |
| `fastembed` / `qdrant-client` | Apache-2.0 / Apache-2.0 | OK | |
| `openai`, `httpx`, `trafilatura`, `alembic`, `sqlalchemy`, `asyncpg`, `pydantic` | MIT/BSD/Apache family | OK | |

**Conclusion:** the current stack is commercially clean. **No AGPL, no GPL linking, no JVM.**

---

## 2. Candidate additions — licence / maintenance / risk

| Candidate | Licence | Commercial | Maintainer health | CPU/RAM cost | Verdict |
| --- | --- | --- | --- | --- | --- |
| **pdfplumber** (+ `pdfminer.six`) | MIT / MIT | OK | **single primary maintainer** (bus-factor risk; MIT + wide vendoring mitigate) | pure Python; moderate RAM on complex pages; no native build | **strongest lightweight candidate** — approval-gated |
| **Docling** | MIT code, models typically Apache-2.0 | OK | active (IBM) | **heavy**: torch/ONNX + **~500 MB model artifacts**, **hundreds of MB RAM per document**, slow on CPU | **defer** — operator-only, flag-gated, benchmark first |
| **PaddleOCR / PP-OCRv5** | Apache-2.0 | OK | active | PaddlePaddle runtime heavy; ONNX path lighter | **defer** — only via the `OCRProvider` seam |
| **RapidOCR (PP-OCRv5 ONNX)** | Apache-2.0 | OK | community | ONNX Runtime, CPU-friendly relative to PaddlePaddle | **evaluate** after the seam exists |
| **PyMuPDF** | **AGPL-3.0** / commercial | **NO without a paid licence** | active | fast, small | **REJECT** for now (ADR 0007 condition noted) |
| **EasyOCR** | Apache-2.0 | OK | moderate | **PyTorch** resident — very heavy | **REJECT** |
| **Apache Tika** | Apache-2.0 | OK | active | **JVM runtime in the image** | **REJECT** (matrix) |
| **Unstructured** | Apache-2.0 (core) | OK | active | heavy, many optional system deps | **REJECT** (matrix) |
| Cloud OCR (Google/Azure/AWS) | proprietary | OK commercially | — | none local | **REJECT** — document bytes must not leave the process |
| `tesseract-ocr-tur` | Apache-2.0 | OK | Debian package | negligible | **conditional** — only if Turkish is approved |

---

## 3. Security review

| Aspect | Assessment |
| --- | --- |
| **Data egress** | No new dependency may send document bytes anywhere. Cloud OCR rejected. Docling must run **offline** (`evaluate_docling.py` already forces `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `enable_remote_services=False`, `allow_external_plugins=False`). |
| **Untrusted input** | Parsing is on untrusted files. Any new parser must be wrapped by the M9 resource ceiling; a hostile file must be able to abort, not hang. |
| **Model artifact provenance** | Docling/PaddleOCR models are downloaded artifacts — a supply-chain surface. They must be pre-provisioned, pinned and hash-verified, never fetched at runtime (matches the existing `--local-models` requirement). |
| **Process isolation** | Tesseract/poppler already run as subprocess argument vectors (no shell). New engines should follow the same pattern; a library that requires in-process native code widens the blast radius. |
| **Secrets** | Unchanged — environment only, `.env.example` committed, never log tokens (AGENTS.md 6). |
| **Prompt injection** | Extracted text is untrusted data (invariant 1). A new engine does not change this, but tests must re-assert it (§TEST_STRATEGY 8). |
| **Supply-chain churn** | `uv sync --frozen` + committed `uv.lock` means every addition is explicit and reviewable. Prefer **one** new dependency at a time. |

**No new dependency is justified by security; several are justified only by accuracy, which is not
yet measured.**

---

## 4. Dependency-conflict analysis

| Candidate | Conflict risk with the pinned tree | Why |
| --- | --- | --- |
| pdfplumber | **LOW** | pure Python; `pdfminer.six` has no native build; does not touch `pydantic`, `sqlalchemy`, `numpy` pins |
| Docling | **HIGH** | large transitive tree (torch or ONNX, `transformers`, `huggingface-hub`, `numpy`, `Pillow`, `pydantic`); model download; version pressure on shared pins |
| PaddleOCR | **HIGH** | `paddlepaddle` wheels; historically fragile on some platforms |
| RapidOCR | **MEDIUM** | `onnxruntime` only |
| EasyOCR | **HIGH** | `torch` |
| Tika | **N/A** | not Python; requires JVM in the image |

Also relevant: `python-docx` pulls **`lxml`** (already present transitively) and the XLSX drawing path
deliberately avoids **Pillow** (parsers.py:605–658 notes openpyxl returns no pictures without it) —
i.e. the codebase has already, correctly, avoided an image dependency. Phase 2 must not reintroduce
one casually.

---

## 5. Resource requirements

| Configuration | CPU | RAM | Disk | Notes |
| --- | --- | --- | --- | --- |
| **Current image** (incumbent stack) | modest | modest | small | pypdf/docx/openpyxl are pure Python; Tesseract/poppler are apt packages |
| **+ pdfplumber** | slightly higher per complex page | + moderate per document | small | pure Python, no models |
| **+ Docling (adopted)** | **significantly higher**; CPU-only inference is the slow path | **hundreds of MB per document** + model residency | **~500 MB models** + torch/ONNX | must be an explicit capacity decision on a single VM also running `postgres`, `qdrant`, `searxng` |
| **+ PaddleOCR/RapidOCR** | higher per image | + model residency | tens–hundreds of MB | ONNX path preferred |
| **+ `tesseract-ocr-tur`** | negligible | negligible | a few MB | just a language pack |

The deployment is **one VM** running `app`, `worker`, `postgres`, `qdrant` and `searxng`
(AGENTS.md). Docling's footprint is a **capacity decision the owner must make**, not a purely
technical one.

---

## 6. Decisions (proposed, pending approval)

| Decision | Proposal |
| --- | --- |
| Keep pypdf / python-docx / openpyxl | **YES** — they work; the gaps are structure, not extraction |
| Add pdfplumber | **ONLY IF** M0/M11 shows a measurable table/layout win at LOW conflict risk. It is the single most defensible addition. |
| Add Docling | **DEFER** — benchmark-only behind `OCRProvider`/adapter seam and a flag; no runtime adoption without an explicit owner capacity decision |
| Add PaddleOCR / RapidOCR | **DEFER** — only after the `OCRProvider` seam (M1) exists and a measured az/ru gain is shown |
| Add `tesseract-ocr-tur` | **CONDITIONAL** — only if Turkish support is approved |
| Add PyMuPDF | **REJECT** (AGPL) |
| Add EasyOCR / Unstructured / Tika | **REJECT** (weight / JVM / matrix) |
| Add any cloud OCR | **REJECT** (data egress) |
| Any DB migration | **NOT PROPOSED** by this design |

---

## 7. Removal candidates (unnecessary packages)

None identified. The runtime list is minimal for the current feature set; `trafilatura` is
exactly pinned and used by the web phase. **No package is proposed for removal.**

---

## 8. Approval gates

No dependency change may proceed without:

1. A **measured** justification (corpus `sha256` + command + version + host profile), and
2. An explicit owner decision recorded in
   [OPEN_SOURCE_INTEGRATION_MATRIX.md](file:///c:/Users/User/Downloads/ai_assistand/telegram-ai/docs/architecture/OPEN_SOURCE_INTEGRATION_MATRIX.md) and, if adopted, an **ADR**, and
3. A regenerated, reviewed `uv.lock`, and
4. The dependency kept **out** of the default runtime image until adopted (optional group / operator
   tooling first).

---

## 9. Summary

- The current dependency set is **licence-clean, small and CPU-friendly**. The baseline needs no
  emergency change.
- The only addition that is defensible **now** is **pdfplumber** (MIT, pure Python, LOW conflict
  risk) — and even that must be gated on a measured win.
- **Docling and PaddleOCR stay deferred**: either may be a real accuracy upgrade, but both carry a
  model/runtime cost that must be an explicit owner capacity decision, and neither has a single
  recorded result in this repository.
- **PyMuPDF stays rejected** for AGPL; ADR 0007's re-evaluation condition (rendering / advanced
  layout / visual analysis) is now *relevant*, but re-evaluation is not adoption.
- **No dependency was installed during this investigation.**
