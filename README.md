# Office Assistant

Private internal company AI assistant foundation. The planned primary interface is a Telegram bot in Azerbaijani, backed by company data, documents, and controlled external search.

## Current status (2026-10-10)

The codebase contains the completed Phases 0-11 and the **Phase 12A image foundation**.
The separate reminder and live-test remediation blocks are also recorded in
[ROADMAP](docs/ROADMAP.md); their historical numbering differs from the owner's
Master Spec phase numbering.

Implemented capabilities include:

- private Telegram authentication and role-based administration;
- PostgreSQL 17 business records, ingestion sessions, audit history and a durable worker;
- PDF/DOCX/XLSX/TXT processing, image asset registration and embedded image provenance;
- Qdrant hybrid retrieval, grounded internal responses, source citations and original-file delivery;
- structured extraction with review, product pricing, payments and proposals;
- explicitly separated internal search (`/baza`), external web search (`/internet`) and
  combined internal/external search (`/tamaxtar`);
- staged product-reference photo confirmations finalized by `/saxla`, cancelled by
  `/cancel`, and revocable by an administrator through `/leqv_et`.

Phase 12B (OCR and visual model benchmarks), Phase 12C (multimodal product search),
visual internet search, disaster-recovery verification and final production UAT **are not
implemented or approved as complete**. Automated test results do not substitute
for live Telegram tests with real accounts and a representative product corpus.

The owner's separate Master Specification (dated 2026-10-07) is the primary product
contract when furnished to an agent. It has not been published in this public
repository. `docs/SPEC.md` is an earlier reconciled design record; see
`AGENTS.md` and `docs/OPEN-QUESTIONS.md` for precedence and later decisions.

## Phase 12B evaluation groundwork

Offline evaluation tools now cover both visual product ranking and OCR product-code
extraction against manually verified, private labels. Neither tool runs an OCR or
embedding model, uploads company images, or modifies the business database. See
[Phase 12B benchmark protocol](docs/PHASE12B-BENCHMARK.md).

Evaluate local OCR candidate outputs with:

```powershell
uv run --no-sync python -m office_assistant.scripts.benchmark_ocr --truth var/benchmark/private/ocr-truth.jsonl --predictions var/benchmark/private/ocr-predictions.jsonl
```

Actual local OCR/embedding model benchmarks and Phase 12C search are still pending.

## Prerequisites

- Python 3.12
- `uv`
- Docker Desktop (for the container smoke test)

Python 3.12 is selected for the project and is enforced by `pyproject.toml`.

## Local setup

```powershell
uv sync --dev
Copy-Item .env.example .env
uv run python -m office_assistant.main
```

Set `POSTGRES_PASSWORD` in `.env` before starting PostgreSQL. When running Python directly on Windows, use `POSTGRES_HOST=localhost`. Inside Compose, the application uses `POSTGRES_HOST=postgres`.

For the local search projection, start Qdrant as well. Its Compose volume is
persistent and its HTTP port is intended for local development only:

```powershell
docker compose up -d postgres qdrant
uv run alembic upgrade head
docker compose up app worker
```

The worker creates `document_chunks_v1` and the `document_chunks` alias on the
first successful index job. FastEmbed weights are cached in `model_cache`.
The synthetic local benchmark is run with:

```powershell
uv run python -m office_assistant.scripts.benchmark_retrieval
```

## Phase 6 AI usage

Set `OPENAI_API_KEY` for the Telegram application and, when Phase 8 extraction
is enabled, for the worker process. Parsing, migrations, indexing, retrieval,
and deterministic benchmarks do not require it. The
initial cost-efficient default is `gpt-6-luna`; `gpt-6.1-sol` is configured for
future deliberate escalation and is not automatically used.

The pinned official OpenAI Python SDK is `3.24.0` (Apache-2.0). It uses
`AsyncOpenAI.responses.parse` with Pydantic structured outputs and `store=false`.

Compose reads OpenAI and Telegram credentials directly from the local `.env`
service env file, so inherited shell credentials cannot replace them. Python
commands outside Docker still follow normal environment-over-dotenv precedence.
After editing `.env`, recreate the app. After code changes, also rebuild:

```powershell
docker compose up -d --build app worker
docker compose exec -T app /app/.venv/bin/python -m office_assistant.scripts.check_openai
```

The explicit diagnostic makes two small, billable synthetic requests using the
same model and structured schemas as the bot. It prints only status, model and
safe error codes. No API keys, prompts or raw exception bodies are printed.
An HTTP 429 alone does not distinguish rate limits from exhausted credit; check
the logged `code`. Optional Telegram typing failures no longer abort answers.
Answer delivery retries transient network errors up to three attempts without
re-running the model. A timeout after Telegram accepted a message can produce a
duplicate reply; Telegram offers no send-message idempotency key.

`/baza <sual>` uses internal company knowledge only. Depending on the question,
it can combine authoritative PostgreSQL business-tool results with Qdrant document
evidence. With valid evidence it returns a concise Azerbaijani answer and unified
provenance citations. If neither channel yields evidence, it returns
`Bu məlumat daxili sistemdə tapılmadı.` without a synthesis call. It never asks
the general model to guess an internal fact and never uses web search.

Ordinary active-user text is routed through deterministic fast paths and, only
when needed, one bounded structured planning request. Company-specific questions
use internal evidence; obvious general questions retain Azerbaijani general chat.
If an open `/saxla` session exists, the text remains an ingestion instruction.
Conversation history is application-owned and bounded by
`CHAT_HISTORY_MAX_MESSAGES` and `CHAT_HISTORY_MAX_TOKENS`; usage is stored in
`ai_usage` without permanent price assumptions.

Before real use, follow the manual gate in
`docs/architecture/phase6-real-corpus-check.md` with 5-10 non-confidential
representative documents. Do not commit those documents or answers.

## PostgreSQL and migrations

```powershell
docker compose up -d postgres
uv run alembic upgrade head
uv run python -m office_assistant.main
```

Schema changes are migration-driven. Application startup does not run `create_all` or silently apply migrations.

## Structured business data

Phase 7 stores products, customers, aliases, historical prices, payments,
business events, proposals, stored-file links, and proposal items in PostgreSQL.
These rows are authoritative business facts. Qdrant remains a searchable
projection of unstructured documents, while OpenAI is limited to language
understanding and synthesis.

Product codes are NFKC-normalized, case-normalized, and stripped only of spaces,
hyphens, and underscores. Customer names are NFKC/casefold normalized with
collapsed whitespace and conservative punctuation handling; suffixes such as
`MMC`, `LLC`, and `LTD` are not removed. Normalized aliases have one owner and
conflicts are explicit.

Money uses `NUMERIC(18,4)` and an independent three-letter currency code. Prices
are immutable history rows with separate general and customer-specific scopes.
Resolution returns `FOUND`, `NOT_FOUND`, or `CONFLICT`; it never chooses between
equally current records. Customer-specific lookup does not silently fall back to
a general price. ADMIN supersession keeps history but excludes the old row from
resolution. Payment totals are grouped by currency.

Important structured rows reference bounded `source_references` provenance.
Provenance may point to a Telegram actor, ingestion session/item, stored file,
parse run, and a small JSON locator such as sheet and row. Full source text is not
duplicated. Business mutation, provenance, and audit writes commit atomically.

To verify the database explicitly during a smoke check, set `DATABASE_CHECK_ON_STARTUP=true` and run the application after PostgreSQL is healthy.

## Candidate-first structured extraction

`/saxla` permanently saves raw knowledge first. Parsing and Qdrant indexing remain
valid even when AI extraction fails or the user rejects its candidates.
`/saxla <text>` creates or appends an ordered text source and commits it through
the same durable pipeline.

After indexing, the PostgreSQL worker reads normalized source blocks directly,
skips OCR/vision-only content, builds bounded batches, and asks `gpt-6-luna` for
strict allowlisted candidate JSON. The LLM cannot mutate business tables. Source
IDs, Decimal values, currencies, dates, identities, duplicates, and conflicts
are validated deterministically.

The uploader receives a compact Telegram summary with `Yaddaşa yaz`,
`Problemlərə bax`, and `Ləğv et`. Only the session owner or ADMIN may review it.
Confirmation applies safe candidates through Phase 7 services; conflicts remain
unapplied. Proposal plus items is atomic, while independent records use separate
transactions. Repeated confirmation does not reapply completed candidates.

Large worksheets are split by `STRUCTURED_EXTRACTION_BATCH_SIZE` and
`STRUCTURED_EXTRACTION_MAX_INPUT_CHARS`; candidate rows are never dumped into
Telegram. Every batch stores model, source range, status, latency, and token usage.

## Typed PostgreSQL business tools

Phase 9 exposes a provider-independent `BusinessToolRegistry` for deterministic,
read-only access to authoritative products, customers, prices, payments, events,
and proposals. Inputs and outputs are strict Pydantic models. Only ACTIVE users
may execute tools; inactive entities are hidden by default and only ADMIN may
explicitly include them. List results default to 20 rows and reject limits above
100. Date boundaries are inclusive.

Customer-specific price lookup does not fall back to a general price unless
`allow_general_fallback=true` is explicitly supplied. Price conflicts and
ambiguous identities remain explicit. Payment totals stay separated by currency.
Proposal comparison uses product IDs or unique normalized raw labels, performs
all arithmetic with `Decimal`, and does not subtract values across currencies.

PostgreSQL structured tools and Qdrant document retrieval answer different kinds
of questions and may be used together. Structured tools answer authoritative
state such as the current KA45 price, an ABC MMC payment total, or deterministic
proposal differences. Qdrant provides source wording such as warranty terms,
special clauses, and prices recorded in documents. When values differ, the answer
preserves both origins and states the difference without declaring a document old
or invalid unless provenance proves it.

The development harness requires neither OpenAI nor Qdrant:

```powershell
uv run python -m office_assistant.scripts.test_business_tools --schemas
uv run python -m office_assistant.scripts.test_business_tools `
  --tool find_product --actor-telegram-id 123456 `
  --payload '{"code_or_alias":"KA45"}'
```

## Bounded internal evidence orchestration

Phase 10 keeps orchestration in application code. Deterministic fast paths cover
common price, payment, proposal, document-term, general-chat, and source-file
requests. An unresolved route may make one structured planner call. A turn may
execute at most four names from the fixed Phase 9 registry; actor identity and role
are constructed by the application and are never accepted from model arguments.
There is no autonomous tool loop or agent framework.

The typed `EvidenceBundle` keeps `STRUCTURED_AUTHORITATIVE` and
`INTERNAL_DOCUMENT` separate, preserves Phase 9 result states and warnings, and
adds application-detected conflicts. Only application-issued evidence IDs can
become citations; fabricated IDs are removed. Synthesis receives evidence as
untrusted data and has a deterministic fallback summary.

Phase 10 treats `INTERNAL_RAG_LIMIT` as an upper bound. Its document adapter
accepts hybrid results only when they pass `INTERNAL_RAG_MIN_SCORE`; a query with
a machine-like identifier additionally requires an exact separator-insensitive
identifier match and uses `INTERNAL_RAG_IDENTIFIER_MIN_SCORE`. These defaults are
`0.55` and `0.30`, based on the synthetic/live Phase 10 corpus where relevant
KA45 evidence scored `0.833333` and unrelated tails scored `0.5` or less. This is
separate from the domain-neutral Phase 5 adapter, which still exposes raw ranked
results to callers.

Final citations contain only validated model-used evidence plus deterministic
conflict records. They are deduplicated by provenance and record identity, then
renumbered before persistence. Proposal deltas are explicitly `after - before`;
latest-proposal detail questions load proposal items without another planner
round. Routing also recognizes narrow ASCII Azerbaijani and colloquial variants,
while authoritative product/customer normalization remains strict and never
fuzzy-corrects codes.

Recent assistant citations are mapped in `ai_message_sources` without copying
source contents. Mappings expire after `RECENT_SOURCE_CONTEXT_TTL_MINUTES` and are
limited to `RECENT_SOURCE_CONTEXT_MESSAGES` assistant messages per user's internal
conversation. Requests such as `2-ci mənbəni göndər`, `PDF-i göndər`, and
`onu göndər` resolve server-side. Ambiguous requests ask for a source number.
Only an ACTIVE user's own recent mapping can resolve to an ACTIVE `file_objects`
row and an original blob under `FILE_STORAGE_PATH/blobs`; normalized artifacts,
storage keys, and model-generated paths are never sent.

## External web evidence

Phase 11 adds two explicit commands. `/internet <sual>` searches only public web
sources and never calls PostgreSQL business tools or Qdrant. `/tamaxtar <sual>`
compares internal structured/document evidence with public web evidence while
keeping `STRUCTURED_AUTHORITATIVE`, `INTERNAL_DOCUMENT`, and `EXTERNAL_WEB`
separate. `/baza` remains strictly internal. Web facts are ephemeral evidence and
never create or update products, prices, customers, payments, proposals, or Qdrant.

SearXNG is the primary provider. Compose pins the official image to
`2026.10.4-d48c4b555` and an immutable multi-platform digest, mounts the small
project-owned `searxng/settings.yml`, persists `/var/cache/searxng`, enables JSON,
and binds its optional debug port to `127.0.0.1` only. Google and DuckDuckGo are
the V1 general engines so an individual engine CAPTCHA/failure can remain partial;
Wikipedia supplies a stable reference source. This requires no Redis/Valkey. Start it with:

```powershell
docker compose up -d searxng
curl.exe "http://127.0.0.1:18080/search?q=Python&format=json"
```

Tavily is an optional fallback. It is called only when SearXNG is unavailable,
times out, returns malformed data, or returns no usable result. Providers are not
queried in parallel. The PostgreSQL `web_search_cache` stores provider-neutral
results, not final model prose. General searches default to a 24-hour TTL;
fresh/current/news/price intent defaults to 15 minutes. All relevant parameters
are part of the key.

Search and page fetch are separate. Only the strongest few results are fetched.
The fetcher permits public HTTP/HTTPS destinations, validates DNS and every
redirect, blocks local/private/link-local/multicast/metadata/internal destinations,
limits time, redirects and bytes, and accepts HTML/plain text only. Trafilatura
2.3.0 (Apache-2.0) extracts readable text before bounded excerpts reach OpenAI.
JavaScript-only pages are treated as unavailable. Recent external citations return
their URL; they are never downloaded or sent as internal company files. Phase 11
does not include image search, OCR, browser rendering, or automatic web routing.

## Telegram Phase 2

Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ACCESS_PASSWORD`, and `TELEGRAM_POLLING_ENABLED=true` in `.env`. Run migrations first, then start the application:

```powershell
uv run alembic upgrade head
uv run python -m office_assistant.main
```

Local mode uses long polling and private chats only. The first user who submits the shared access password becomes the active `ADMIN`. Later valid requests become `PENDING` until the administrator approves them from `/users` using inline buttons. Active users are recognized by their numeric Telegram user ID and are not asked for the shared password again.

Implemented commands are `/start`, `/whoami`, `/users`, `/saxla`, `/cancel`,
`/baza`, `/internet`, and `/tamaxtar`. `/users` is server-side ADMIN-only. User roles and statuses are persisted
in PostgreSQL and security-sensitive mutations are written to `audit_events`.

The shared access password is a one-time entry secret, not a permanent user password. It is never stored in PostgreSQL or logs. Failed attempts have a bounded in-process cooldown suitable for the small V1 deployment. Telegram usernames are display metadata only; numeric Telegram IDs are authoritative.

## Raw file ingestion

Active ADMIN and USER accounts may upload PDF, DOCX, XLSX, TXT, JPG, JPEG, and PNG files in a private chat. Files are staged under `FILE_STORAGE_PATH/staged` and are not treated as company knowledge. Captions and ordinary text sent while a session is open are preserved in order as session items.

`/saxla` is the commit boundary. It moves staged bytes into `FILE_STORAGE_PATH/blobs`, marks the session committed, creates exactly one `PENDING` `ingestion_jobs` row, and returns a compact Azerbaijani summary. Exact duplicate bytes are identified by incremental SHA-256; duplicate submissions retain their own session item/provenance while reusing the physical blob.

`/cancel` cancels the user's open session and removes unreferenced staged files. Open sessions expire lazily after `PENDING_SESSION_TTL_HOURS`; deterministic cleanup is available with:

```powershell
uv run python -m office_assistant.scripts.cleanup_staging
```

`MAX_UPLOAD_BYTES` and `MAX_FILES_PER_PENDING_SESSION` are operational safety controls, not product-level file-count limits. No parser, OCR, embedding, AI call, or worker runs during upload or `/saxla`.

## Quality checks

Integration tests delete records and must use a disposable database whose name
ends in `_test`, never the running bot's database. Apply migrations to that test
database first. Unit tests can run without it using `uv run pytest -m "not integration"`.

```powershell
uv run pytest
uv run pytest -m integration
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

## Docker

```powershell
docker compose build
docker compose run --rm app
```

Compose runs `app`, `worker`, `postgres`, `qdrant`, `searxng`, and
`model-cache-init`. The app and worker share the same
persistent `app_storage` volume at `/data/storage`. The worker does not receive
the Telegram token; Phase 8 gives it only the OpenAI key needed for structured
extraction:

The one-shot `model-cache-init` service prepares the persistent FastEmbed cache
for the unprivileged application user before app and worker startup.

```powershell
docker compose up -d postgres
uv run alembic upgrade head
docker compose up app worker
```

The worker polls PostgreSQL, claims jobs with `FOR UPDATE SKIP LOCKED`, uses leases for crash recovery, and retries infrastructure failures up to `WORKER_MAX_ATTEMPTS` (default 3). A job is `SUCCEEDED` when all unique files succeed or are intentionally deferred, `PARTIAL` when at least one file succeeds/deferred and another fails, and `FAILED` when no file produces a usable result. Per-file states are `SUCCEEDED`, `DEFERRED`, or `FAILED` after processing.

Normalized artifacts use `FILE_STORAGE_PATH/derived/<file-id>/<parse-run-id>/normalized.json` and `text.txt`. A generic outbox event is written transactionally at job completion; the Telegram application sends a compact Azerbaijani summary without exposing extracted text or filesystem paths. `jobs` command UI is intentionally not added; operations use PostgreSQL logs and metadata.

Successful parse jobs create a durable knowledge-index job. PostgreSQL state is
authoritative, while Qdrant stores deterministic chunk points as a rebuildable
projection. Retrieval supports dense, BM25, and hybrid RRF modes with file,
parse-run, and media-type filters. Empty results are valid; no arbitrary score
threshold is applied.

Implemented parser support is deterministic: PDF pages retain page locators, DOCX block order/tables are preserved, XLSX formulas are recorded but never evaluated, TXT keeps line ranges, and images are marked `requires_vision=true`. Image-only PDF pages are marked `requires_ocr=true`; OCR is not run. Parsing is synchronous library work executed behind a controlled `asyncio.to_thread` boundary so Telegram handlers remain responsive. A stuck native parser can still occupy a worker thread; process isolation is a future operational hardening step.

Runtime parser dependencies are pinned by `uv.lock`: pypdf 6.19.0 (BSD-3-Clause), python-docx 1.2.0 (MIT), and openpyxl 3.1.5 (MIT). PyMuPDF is intentionally excluded because its AGPL/commercial dual licensing is unnecessary for the current narrow PDF requirements. Qdrant client 1.19.1 and FastEmbed 0.8.1 are Apache-2.0; Compose Qdrant is pinned to 1.19.0 (Apache-2.0). The selected MiniLM model is Apache-2.0, Potion is MIT, and `Qdrant/bm25` is Apache-2.0. Docling is postponed for a real-corpus benchmark.

## Dependency policy

Runtime dependencies: `pydantic-settings` provides typed environment configuration; SQLAlchemy 2.x provides the typed async ORM/Core layer; `asyncpg` provides the async PostgreSQL driver; Alembic provides migration management. Trafilatura 2.3.0 (Apache-2.0) adds about a 158 KB wheel plus parsing/date-language dependencies and can be replaced behind `WebContentFetcher`. SearXNG is AGPL-3.0-or-later as a separately deployed network service; its source availability and deployment notices remain an operational distribution concern. Tavily is an external SaaS and no Tavily SDK code is bundled. Each can be replaced behind the persistence boundary later, though the current stack is the approved foundation.

Trafilatura's extraction tree is otherwise permissive (Apache-2.0/BSD/MIT/CNRI).
The transitive `tld` 0.13.2 metadata offers `MPL-1.1 OR GPL-2.0-only OR
LGPL-2.1-or-later`; this project relies on the MPL option, which is file-level
copyleft and should remain visible in any distribution review. This is package
metadata and an engineering concern, not a legal conclusion.

Development dependencies: `pytest` for tests, `ruff` for linting/formatting, and `pyright` for static type checking. Logging uses the Python standard library, so no observability stack is added.

`uv` is used for project dependencies, the lock file, local commands, and Docker installation. Do not introduce Poetry, pipenv, Conda, or another dependency manager.

## Secrets

Never commit Telegram tokens, OpenAI keys, database passwords, Tavily/Exa keys, backup credentials, or production `.env` files. `.env.example` contains placeholders only.

