# Baseline Audit — `f1r10/telegram-ai` before the Global Intelligence Engine

Phase 0 evidence record. Every claim below points at a file, a command or a number.

## 1. Git state

| Item | Value |
| --- | --- |
| Remote | `https://github.com/f1r10/telegram-ai.git` |
| Upstream default branch | `main` |
| `main` HEAD at clone time | `e3d04ba654e836d5c2616eac24e26104d7312319` ("Milestone 3: deliver order reminders durably without duplicates (#175)") |
| Working branch | `feature/global-intelligence-engine` (created from `main` HEAD) |
| Working tree | clean except the deliberate, documented changes in this branch |

Commands:

```powershell
git clone https://github.com/f1r10/telegram-ai.git telegram-ai
cd telegram-ai
git checkout -b feature/global-intelligence-engine
git rev-parse HEAD   # e3d04ba654e836d5c2616eac24e26104d7312319
```

## 2. Governing documents read

| Document | Status |
| --- | --- |
| `AGENTS.md` (repo root) | Read in full. Ten non-negotiable invariants. One phase at a time; `make test lint typecheck` after every step. |
| `README.md` | Read in full. Phases 0–11 + 12A image foundation delivered. |
| `docs/SPEC.md` | Present (earlier reconciled V1 design). |
| `docs/ROADMAP.md` | Read in full. Authoritative delivered-phase record. |
| `docs/OPEN-QUESTIONS.md` | Present; Q14, Q20, Q27, Q31–Q33, Q46–Q49 referenced by code/tests. |
| `docs/architecture/decisions/0001..0017` | 17 ADRs present. Never delete historical decisions. |
| Owner `telegram_ofis_asistanti_MASTER_SPEC` | **NOT PRESENT** in this environment. Per `AGENTS.md`, its content must not be invented. All statements about it below are quoted only as they appear in repo docs. |

No `AGENTS.md` exists below the repo root (checked `docs/`, `src/`, `tests/`, `scripts/`, `migrations/`).

## 3. Stack actually implemented

Python 3.12 · PostgreSQL 17 (system of record) · Qdrant 1.19 (rebuildable projection) ·
Docker Compose · SQLAlchemy 2 async + asyncpg · Alembic · Pydantic v2 + pydantic-settings ·
python-telegram-bot 21+ · FastEmbed (dense MiniLM + BM25 sparse) · OpenAI Responses API ·
pytest / Ruff / Pyright strict.

Confirmed in `pyproject.toml`, `Makefile`, `docker-compose.yml`.

## 4. Code inventory

### 4.1 Application packages (`src/office_assistant/`)

| Package | Role | Notable modules |
| --- | --- | --- |
| `ai/` | Provider-independent LLM abstraction | `provider.py` (`LanguageModel`, `VisionLanguageModel`, OpenAI + Gemini adapters), `schemas.py`, `service.py` (`GroundedAnswerService`) |
| `application/` | Use-case services | `ingestion.py`, `inventory.py`, `sales_ledger.py`, `orders.py`, `customers.py`, `pricing.py`, `payments.py`, `proposals.py`, `reminders.py`, `reference_images.py`, `feature_permissions.py`, `authentication.py` |
| `auth/` | Capability policy | `features.py`, `policy.py` |
| `business/` | Business normalization + parsing | `normalization.py`, `product_registration.py`, `reference_confirmation.py` |
| `business_tools/` | Allow-listed read-only typed tools | `registry.py` (13 tools), `models.py`, `service.py` |
| `core/` | Settings, logging, clock | `settings.py`, `logging.py` |
| `extraction/` | Candidate-first structured extraction | `service.py`, `review.py`, `schemas.py`, `benchmark.py` |
| `infrastructure/` | DB + storage | `database/models.py` (79 classes), `storage/local.py` |
| `integrations/telegram/` | Telegram transport | `handlers.py` (5 222 lines), `messages.py` (all AZ strings), `update_processor.py`, `outbox.py`, `turn_context.py` |
| `internal_evidence/` | **Phase 10 orchestration** — the current "brain" | `router.py` (deterministic fast paths + one LLM planner call), `service.py` (evidence bundle, citations, synthesis), `document_search.py`, `short_answers.py`, `label_facts.py`, `query_normalization.py`, `delivery.py` |
| `knowledge/` | Qdrant index + retrieval | `qdrant_store.py`, `retrieval.py` (dense/BM25/hybrid RRF), `chunking.py`, `embeddings.py` |
| `miniapp/` | Read-only Telegram Web App | `backend.py`, `http.py`, `auth.py`, `photo_lookup.py`, `stock_*.py` |
| `processing/` | Ingestion pipeline | `parsers.py` (PDF/DOCX/XLSX/TXT + embedded pictures), `worker_service.py`, `pdf_ocr.py`, `image_ocr.py`, `vision.py`, `semantic_visual.py`, `visual_ann.py` |
| `orders/`, `reminders/`, `web_evidence/`, `observability/`, `scripts/`, `workers/` | Supporting services | as documented in `docs/ROADMAP.md` |

### 4.2 Telegram surface (preserved, must not regress)

Commands implemented: `/start`, `/whoami`, `/users`, `/saxla`, `/cancel`, `/baza`, `/internet`,
`/tamaxtar`, `/help`, `/menu`, `/hatirlatmalar`, `/verilerimi_sil`, `/leqv_et`, plus the
one-shot guided menu and the Mini App. All bot-facing strings live in
`integrations/telegram/messages.py`.

### 4.3 Database

27 Alembic migrations, `0001_create_system_metadata` → `0027_order_reminder_delivery`.
Migration history is linear and must never be rewritten.
`infrastructure/database/models.py` defines 79 mapped classes including
`products`, `product_aliases`, `customers`, `product_prices`, `payments`, `business_events`,
`proposals`, `proposal_items`, `source_references`, `file_objects`, `ingestion_*`,
`document_parse_runs`, `knowledge_index_*`, `ai_conversations/messages/message_sources`,
`web_search_cache`, `image_assets`, `image_asset_occurrences`, `product_image_relations`,
`pending_product_image_confirmations`, `inventory_*`, `sales_entries`, `photo_groups`,
`orders`, `order_reminders`, `order_attachments`, `reminders`, `reminder_deliveries`.

**No table exists yet for** canonical entities / aliases beyond products and customers,
entity relationships, relationship evidence, unified evidence records, temporal facts,
or query execution traces. Those are Phase 3/5 gaps (see §7).

### 4.4 Environment variable names (names only, never values)

`APP_ENV`, `LOG_LEVEL`, `POSTGRES_*`, `DATABASE_CHECK_ON_STARTUP`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_ACCESS_PASSWORD`, `TELEGRAM_POLLING_ENABLED`, `MINIAPP_*`, `OPENAI_API_KEY`,
`AI_PROVIDER`, `GEMINI_*`, `WEB_SEARCH_*`, `SEARXNG_*`, `TAVILY_API_KEY`, `EXA_API_KEY`,
`FILE_STORAGE_PATH`, `MAX_UPLOAD_BYTES`, `MAX_FILES_PER_PENDING_SESSION`,
`PENDING_SESSION_TTL_HOURS`, `WORKER_*`, `PDF_OCR_*`, `IMAGE_OCR_ENABLED`, `QDRANT_*`,
`EMBEDDING_*`, `CHUNKING_*`, `RETRIEVAL_DEFAULT_LIMIT`, `OPENAI_DEFAULT_MODEL`,
`OPENAI_STRONG_MODEL`, `VISION_*`, `VISUAL_SEMANTIC_*`, `RAG_*`, `CHAT_HISTORY_*`,
`AI_RATE_LIMIT_PER_MINUTE`, `TELEGRAM_MAX_MESSAGE_LENGTH`, `STRUCTURED_EXTRACTION_*`,
`INTERNAL_TOOL_MAX_CALLS`, `INTERNAL_RAG_*`, `RECENT_SOURCE_CONTEXT_*`,
`FILE_DELIVERY_MAX_BYTES`. Source: `core/settings.py`, `.env.example`.

`.env` was never opened, printed or committed.

## 5. Baseline test results

Command:

```powershell
uv sync --dev
uv run --no-sync pytest -m "not integration" -q --tb=no --junitxml=baseline.xml
```

| Metric | Value |
| --- | --- |
| Collected | **1341** |
| Passed | **1299** |
| Skipped | **2** |
| Failed | **40** |
| Errors | 0 |

**Every one of the 40 failures is a native-Windows host limitation, not a product defect.**
They were grouped from the JUnit XML failure messages:

| Count | Cause | Example |
| --- | --- | --- |
| 21 | POSIX shell scripts invoked with a Windows path (`bash -n C:\...`) — bash mangles `C:` to `CUsersUser...` | `tests/unit/test_wsl2_staging_preflight.py`, `test_manual_uat_gate.py`, `test_photo_staging_rollout.py`, `test_staging_compose_isolation.py`, `test_staging_visual_activation_script.py` |
| 10 | `os.getuid()` does not exist on Windows | `tests/unit/test_ops_backup_restic.py`, `test_verify_restic_backup.py` |
| 7 | Creating a symlink needs SeCreateSymbolicLinkPrivilege (`WinError 1314`) | `test_v2_semantic_visual.py`, `test_v2_ocr_replay_safety.py`, `test_verified_visual_vector_hot_cache.py` |
| 1 | Windows socket reset (`WinError 10053`) in the Mini App HTTP test | `test_v2_miniapp_http.py` |
| 1 | POSIX mode bits differ from Windows ACL-derived bits | `test_kali_vm_env.py` |

The project targets Linux/WSL2 (`README.md` §Prerequisites; `.github/workflows/quality.yml`
runs on Ubuntu). On Linux these same tests are green — `docs/ROADMAP.md` records
`pytest -m "not integration"` **1341 passing** and `pytest -m integration`
**125 passed / 1 skipped** on the merged `main`.

### 5.1 One harness defect fixed in this branch

`tests/unit/test_v2_miniapp_photo.py::test_invalid_photographs_do_not_start_database_lookup`
parametrised on `b"x" * (4 * 1024 * 1024 + 1)`. Pytest renders the parameter into the node id
and then writes it to the `PYTEST_CURRENT_TEST` environment variable, which Windows caps at
32 767 characters. The result was not a test failure but a **whole-process crash** at setup,
which truncated the entire run (it produced a 16 MB log of `x` characters). Explicit
`ids=[...]` were added. No test semantics changed. This is the only pre-existing test file
modified for the baseline; it is required to run the suite at all on Windows.

## 6. Feature matrix — what exists today

| Capability | Status | Proof |
| --- | --- | --- |
| Telegram auth, roles, ACL, audit | works | `application/authentication.py`, `repositories/audit.py`, `tests/integration/test_phase2_auth.py` |
| Raw file ingestion + dedup by SHA-256 | works | `application/ingestion.py`, `tests/integration/test_phase3_ingestion.py` |
| Durable PostgreSQL worker (SKIP LOCKED + leases) | works | `workers/ingestion.py`, `tests/integration/test_phase4_worker.py` |
| PDF/DOCX/XLSX/TXT parsing + locators | works | `processing/parsers.py`, `tests/unit/test_parsers.py` |
| Embedded picture extraction with locators | works | `processing/pdf_images.py`, `tests/unit/test_phase12a_embedded_images.py` |
| Qdrant hybrid retrieval (dense/BM25/RRF) + ACL filter | works | `knowledge/retrieval.py`, `tests/integration/test_phase5_qdrant.py` |
| Grounded answers with validated citations | works | `ai/service.py`, `internal_evidence/service.py` |
| Allow-listed read-only business tools | works | `business_tools/registry.py`, `tests/unit/test_business_tools.py` |
| Internal evidence routing (deterministic + 1 LLM planner call) | works | `internal_evidence/router.py`, `tests/unit/test_phase10_*` |
| Conflict surfacing (structured vs document money) | partial | `_detect_conflicts` handles money only |
| External web search, SSRF-hardened | works | `web_evidence/*`, `tests/unit/test_phase11_web_evidence.py` |
| OCR (scanned PDF + images), language `aze+eng+rus` | partial | `processing/pdf_ocr.py`; real corpus calibration outstanding (`docs/ROADMAP.md`) |
| Vision reading of an attached photo | partial | `processing/vision.py`; candidate-only, never confirmed identity |
| Product reference photos confirmed by `/saxla` | works | `application/reference_images.py`, migration `0016`/`0017` |
| Structured candidate extraction with human review | works | `extraction/*`, migration `0011` |
| Reminders (UTC + IANA tz, confirm-first) | works | `reminders/*`, migration `0014` |
| Orders domain + delivery engine | works | `orders/*`, migrations `0026`/`0027` |
| Inventory ledger, reservations, units | works | `application/inventory.py`, migrations `0020`–`0022` |
| **Confidence labels (Confirmed/Probable/Unknown) in answers** | **missing** | no such type anywhere in `src/` |
| **Typo tolerance across AZ/TR/RU/EN** | **missing** | `query_normalization.py` only strips diacritics; no edit-distance correction |
| **Concept expansion (camera → cable families)** | **missing** | no connector/concept knowledge base |
| **Connector/port compatibility reasoning** | **missing** | `_detect_conflicts` is money-only; no port↔connector model |
| **Canonical entity registry + typed relations w/ evidence** | **missing** | only `products`/`customers`; no relation table |
| **Temporal facts (`valid_from`/`valid_until`) for relationships** | **missing** | `product_prices` has history; relationships have no validity window |
| **Query execution traces / retrieval evaluation records** | **missing** | no table |
| **Missing-information detection surfaced honestly** | partial | `NOT_FOUND_INTERNAL_AZ` exists but no per-claim Unknown label |
| **Evaluation harness with a scenario corpus** | **missing** | `scripts/benchmark_*` only; no A–M scenario runner |

## 7. Volume / resource observations

Docker, Python 3.12 and `uv` are available in this environment. A live Telegram bot token,
an OpenAI/Gemini key, a populated company corpus and a running PostgreSQL/Qdrant pair are
**not** available. Consequences are recorded in `docs/audit/FAILURE_ANALYSIS.md` §6 and in
`docs/progress/PROGRESS.md`: every new component is built to be verifiable **offline and
deterministically**, and no live claim is made without evidence.

## 8. Exit criteria for Phase 0

- [x] Git state recorded (§1).
- [x] All governing documents read (§2).
- [x] Code, DB and env-var inventory produced (§4).
- [x] Baseline test suite run and counted, with every failure explained (§5).
- [x] Feature matrix with proof (§6).
- [x] Failure reproduction and analysis → `docs/audit/FAILURE_ANALYSIS.md`.
- [x] Target architecture + plan → `docs/architecture/`.
