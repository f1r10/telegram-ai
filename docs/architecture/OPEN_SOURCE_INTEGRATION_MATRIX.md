# Open-Source Integration Matrix

Required columns (§7 of the task brief): **Component · Problem · Existing alternative · Why
chosen/rejected · Resource cost · Integration complexity · Benchmark result · Decision.**

Baseline facts used below are from [BASELINE_AUDIT.md](../audit/BASELINE_AUDIT.md). License and
resource figures were checked against upstream sources on 2026-11-12; each row cites its source.

**Decision principle (§7):** a small number of well-integrated, benchmark-justified components beats
3–4 overlapping systems. No component is added because it is popular (§2 rule 8).

---

## 1. Summary table

| Component | Problem it would solve | Existing alternative in this repo | Why chosen / rejected | Resource cost | Integration complexity | Benchmark result | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Qdrant** | hybrid dense+sparse retrieval, metadata filters | already present | already the projection layer; no alternative needed | running service (already deployed) | none (in place) | baseline hybrid retrieval already measured by `knowledge/benchmark.py` | **KEEP (already integrated)** |
| **Docling** | hard PDFs: tables, multi-column, scans, embedded images | `processing/parsers.py` (pypdf/openpyxl/python-docx) + `processing/pdf_ocr.py` | MIT code / Apache-2.0 weights; local, no service; solves table-structure recovery which the baseline parser cannot do; repo **already has** an offline comparison harness (`scripts/evaluate_docling.py`, `docs/DOCLING-EVALUATION.md`) | CPU-only works; no server, no extra container; per-page model inference on CPU (~seconds/page) | **Low**: pure-Python, behind a `DocumentParser` adapter; no new service | NOT MEASURED YET — must be produced in Phase 2 by running the existing `scripts/evaluate_docling.py` on the §17.1 fixture corpus | **EVALUATE IN PHASE 2**; adopt behind the adapter only if it beats the baseline parser on the table/scan fixtures |
| **PaddleOCR** | OCR of scans, labels, model/serial numbers, phone photos; multilingual | `processing/image_ocr.py` + `processing/pdf_ocr.py` | Apache-2.0; PP-OCRv6 covers 50 languages in one model including Latin-script (Azerbaijani/Turkish) and Russian; the baseline OCR quality on AZ/RU is unmeasured | CPU inference; model download ~tens of MB; no service | **Medium**: new dependency (paddle), new `OCRProvider` adapter; benchmark per language needed | NOT MEASURED YET — Phase 2 must produce per-language accuracy (AZ/TR/RU/EN) before adoption | **EVALUATE IN PHASE 2**; keep existing OCR as default until it loses on measured accuracy |
| **RAGFlow** | deep parsing + built-in rerank + citations, as a provider | the whole existing PostgreSQL + Qdrant + `internal_evidence` path | Apache-2.0, but it ships its **own** doc-store, object storage, DB and cache, i.e. a second system of record — directly conflicts with §1.2 "PostgreSQL as system of record, Qdrant as rebuildable projection". Min. 4 CPU / 16 GB RAM / 50 GB disk before any corpus. | **High**: ≥4 CPU / 16 GB RAM / 50 GB disk; adds Elasticsearch + MinIO + MySQL containers | **High**: separate Compose profile, data-sync semantics, rollback story for a duplicated corpus | NOT MEASURED; cannot be measured on the available host (see environment note) | **EVALUATE ONLY, in an isolated `eval` Compose profile.** Not part of the default stack. Adopt only if it beats the baseline on §17.3 metrics *and* the host can afford it |
| **LightRAG** | cross-document entity relations, multi-hop, global context | PostgreSQL-native entity/relation tables + recursive CTEs (Phase 3) | MIT; but it introduces a second graph store and corpus duplication. §7 and §9 both say: build PostgreSQL-native first, adopt only if multi-hop benchmarks prove it insufficient. | moderate (embeddings + its own storage backends) | **High**: storage backend decisions, sync cost, deduplication with the existing corpus | NOT MEASURED — gated on the Phase 3 multi-hop benchmark | **BENCHMARK-GATED (Phase 3+)**. Do not install pre-emptively |
| **Graphiti** | temporal knowledge graph (price history, changing relations) | PostgreSQL `valid_from` / `valid_until` columns on `entity_relations` (Phase 3) | Apache-2.0; gives bi-temporal facts with provenance, **but** requires operating a graph database (Neo4j/FalkorDB) and §7 explicitly says do not run LightRAG and Graphiti together. The required temporal distinction (scenario E) is already expressed by two timestamp columns. | **High**: a graph DB service on top of PostgreSQL | **High**: new datastore, new provider, new failure mode | NOT MEASURED — gated on the Phase 5 temporal benchmark | **BENCHMARK-GATED (Phase 5+)**. PostgreSQL temporal facts are the default |
| **Apache Tika** | exotic format coverage (e.g. legacy Office, ODF) | baseline parsers cover PDF/DOCX/XLSX/CSV/TXT/images | solves a gap that the corpus in §17.1 does **not** contain | JVM service | High (new JVM service) | n/a | **REJECT (no proven gap)**. Revisit only if a real unsupported format appears |
| **pgvector** | simple vector fallback inside PostgreSQL | Qdrant (already present, with hybrid + filters) | would duplicate Qdrant for no measured gain | none (extension) | Medium | n/a | **REJECT (duplicate capability)** |
| **Sentence-Transformers** | alternative embedding models | FastEmbed already serves the multilingual MiniLM dense model + BM25 sparse | §14 requires choosing the embedding model by a **real Azerbaijani benchmark**, not by reputation. FastEmbed can already load other models, so no new library is needed to benchmark alternatives. | none beyond existing | Low if only used to test alternative checkpoints | NOT MEASURED YET — Phase 2 should record an AZ retrieval benchmark across candidate embedding models | **EVALUATE AS A MODEL CHOICE, NOT AS A NEW LIBRARY** |
| **Unstructured** | broad format parsing | Docling + existing parsers | heavy dependency tree; overlapping with Docling | High | High | n/a | **REJECT (overlaps Docling)** |

---

## 2. Environment note (honesty requirement, §2 rule 5)

Docker 29.4.3 is installed on the analysis host, but there is **no populated corpus, no live
Telegram token and no LLM API key** (see `BASELINE_AUDIT.md` §7). Therefore:

* No component above has a live benchmark number yet. Every "Benchmark result" cell that reads NOT
  MEASURED is exactly that — it is not an estimate and must not be read as approval.
* RAGFlow in particular **cannot** be resource-evaluated on this host: 16 GB RAM minimum plus
  Elasticsearch, MinIO and MySQL would exceed a typical developer machine and is well beyond what
  the acceptance run needs. It stays a documented, isolated-profile option.
* Phase 2 is responsible for turning the Docling and PaddleOCR cells into measured numbers using the
  repo's own offline harnesses (`scripts/evaluate_docling.py`, `scripts/benchmark_ocr.py`).

---

## 3. Decision log (append-only)

| Date | Component | Decision | Evidence |
| --- | --- | --- | --- |
| 2026-11-12 | Qdrant | KEEP (already integrated) | `docs/architecture/decisions/0008-qdrant-knowledge-index.md` |
| 2026-11-12 | Docling | EVALUATE in Phase 2 behind a parser adapter; not yet adopted | `docs/DOCLING-EVALUATION.md`, `scripts/evaluate_docling.py` exist; no new benchmark yet |
| 2026-11-12 | PaddleOCR | EVALUATE in Phase 2 behind an `OCRProvider`; not yet adopted | existing OCR path unmeasured per language |
| 2026-11-12 | RAGFlow | EVALUATE ONLY, isolated `eval` profile; rejected as default | duplicate system of record; ≥16 GB RAM |
| 2026-11-12 | LightRAG | BENCHMARK-GATED, Phase 3 multi-hop only | PostgreSQL-native graph is the default |
| 2026-11-12 | Graphiti | BENCHMARK-GATED, Phase 5 temporal only | `valid_from`/`valid_until` columns suffice for scenario E |
| 2026-11-12 | Apache Tika | REJECT — no proven gap | §17.1 corpus has no Tika-only format |
| 2026-11-12 | pgvector | REJECT — duplicates Qdrant | Qdrant already deployed |
| 2026-11-12 | Unstructured | REJECT — overlaps Docling | — |
| 2026-11-12 | Sentence-Transformers | EVALUATE as a model choice only | §14 requires an Azerbaijani embedding benchmark |

---

## Sources

- [RAGFlow README (requirements, Docker)](https://raw.githubusercontent.com/infiniflow/ragflow/main/README.md)
- [RAGFlow quickstart docs](https://ragflow.io/docs/)
- [RAGFlow license (Apache-2.0)](https://aitoolsatlas.ai/tools/ragflow)
- [Docling — MIT-licensed toolkit](https://docling.org/)
- [docling on PyPI (license metadata: MIT)](https://pypi.org/project/docling/2.138.0/)
- [Docling paper (MIT-licensed, arXiv 2501.17887)](https://arxiv.org/pdf/2501.17887)
- [PaddleOCR on PyPI (Apache-2.0)](https://pypi.org/project/paddleocr/)
- [PaddleOCR supported languages / PP-OCRv6](https://paddleocr.dev/models)
- [LightRAG (lightrag-hku) on PyPI — MIT](https://pypi.org/project/lightrag-hku/1.5.6/)
- [Graphiti (graphiti-core) on PyPI](https://pypi.org/project/graphiti-core/0.8.0/)
- [Graphiti — Apache-2.0, requires your own graph database](https://hysenlabs.com/projects/getzep-graphiti)
