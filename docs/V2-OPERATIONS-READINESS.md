# Telegram Office Assistant V2 — operational checks

The check is **read-only**; it does not reset PostgreSQL, Redis, Qdrant or Docker volumes and does not call a paid AI model.

Run from the WSL checkout **after deploying the matching GitHub commit**:

```bash
cd ~/telegram-ai
sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T app uv run --no-dev python -m office_assistant.scripts.check_readiness
```

Expected JSON:
- `readiness: READY`: PostgreSQL queries succeed, Qdrant collection endpoint is available, real SearXNG JSON search gives at least one result, the locally installed PDF OCR binaries and configured language packs are available, and existing indexing rows have no recorded failure/deferred warnings.
- `readiness: DEGRADED`: some historical documents still need OCR, local OCR prerequisites are missing or disabled, index failures require operator attention, or the live internet search returned no hits. Do not advertise fully working web search based only on `/healthz`.
- `readiness: NOT_READY`: essential PostgreSQL or Qdrant checks failed.

**Historical partial-PDF recovery** (no source deletion):
```bash
# First: read-only scan
sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T app uv run --no-dev python -m office_assistant.scripts.repair_legacy_index --limit 100
# Only after backup/review and during a controlled maintenance window:
# sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T app uv run --no-dev python -m office_assistant.scripts.repair_legacy_index --apply --limit 100
```

The second command only queues eligible older documents for the existing indexing worker; it does not make OCR scans readable. Re-run the readiness check after the worker has completed indexing. If a job remains failed, inspect app/worker logs with credentials and personal data removed before sharing.

## Real read-only Telegram Mini App (experimental, default OFF)

The `office_assistant.miniapp` package now provides a mobile-first HTML/CSS/JS
client backed by a real signed Telegram API. The interface has **Sual ver**,
**Bizim baza**, **Məhsul tap**, **Anbar qalığı** and **Satış hesabatı**.
All these screens call existing `InternalEvidenceService`,
`BusinessQueryService`, `InventoryLedgerService` and
`SalesLedgerService` read paths. It shows cited source
labels, unknown stock distinctly from confirmed zero, error states, long
answers, mobile safe-area spacing, and dark/light layouts. Financial writes, stock changes and bulk imports **stay in the native
bot**. The Mini App supports only user-confirmed single-file ingestion via
the existing transactional service. The only browser file operation is explicit,
authorized one-tap download of an original cited document; the user can submit a **separately confirmed single-file ingestion** through the web endpoints,
but cannot alter or delete existing company files.

**Critical authentication:** the web API verifies the raw
`Telegram.WebApp.initData` HMAC using the bot-token-derived `WebAppData`
secret, a constant-time hash comparison, a bounded `auth_date` and unique
signed parameters. The verifier accommodates Telegram's newer third-party
`signature` field. It then queries the *current* PostgreSQL user record
on **every** request, requiring `ACTIVE`; no user ID, role, stock quantity,
file path, or permission sent by the browser is ever trusted. The browser
sends the signed data in a request header, never in a URL, cookie or
localStorage. Source answers are text-only with labels; no private blob
paths are sent to the UI. Only known routes are served. The server has
bounded JSON bodies, explicit same-origin checks, no CORS wildcard,
`no-store`, an restrictive CSP and a bounded inference concurrency.
The question, product, stock, sales and source-read APIs are read-only. Separate upload endpoints provide an explicitly user-confirmed single-file ingestion workflow, not inventory or sales mutations.

**Mini App product and sales cards:** `POST /api/product` accepts an exact
product code or alias and only displays active confirmed catalog identity,
or a bounded list of candidates when ambiguous. Product cards do not invent
price or stock. `POST /api/sales` takes a customer name and a separately
selected report year, checking confirmed `POSTED` invoice totals. Its amounts
stay separate by currency and missing entries are **not** declared zero sales.
Both APIs revalidate signed actor identity and DB authorization and never
create products, sales, inventory movements or source documents. The sales
year field is shown only inside the sales mode; default chat remains a free
question box. New documents and write operations still use the native Telegram bot.

**Original citation files:** grounded chat answers expose up to three optional
"Faylı endir" buttons for their actual file-backed sources. Each button uses
an opaque ten-minute, per-actor in-memory ticket bound to **the original
file ID and citation number**. The dedicated `POST /api/source` rechecks
Telegram `initData`, active DB actor, ticket identity/expiry and the latest
eligible source through the existing `SourceFileDeliveryService`. A newer
answer invalidates old tickets; a changed source number/file ID cannot
substitute a newer document. The HTTP server streams the committed bytes
with `Content-Disposition: attachment`, `nosniff`, `no-store` and a
bounded maximum size, without exposing storage paths or permanent URLs.
Ordinary browser links, unsolicited file retrieval and group-chat export
are not enabled. A reboot invalidates all temporary tickets, fail-closed.
Test actual file downloads in Telegram Android/iOS before enabling staging.

The HTTP server binds **127.0.0.1 only**, in its own process, independently
of Telegram polling. It is not automatically exposed by Docker Compose.
A qualified operator must stage it with an approved **HTTPS hostname and
reverse proxy** that provides TLS, sensible connection/request limits,
safe forwarded headers and firewall policy. No direct WAN port is allowed.
The `MINIAPP_PUBLIC_ORIGIN` must match the complete HTTPS origin sent to
Telegram and the server. Do not set it to HTTP or an IP address.

```bash
# Operator-managed WSL staging only; use live secrets and backed-up local DB
# in the private .env, never commit those values.
MINIAPP_ENABLED=true
MINIAPP_PUBLIC_ORIGIN=https://office.example-company.com
MINIAPP_LOCAL_PORT=18791

# With private env configured for host PostgreSQL/Qdrant:
uv run --no-dev python -m office_assistant.miniapp.server
# Operator's HTTPS reverse proxy forwards exactly to http://127.0.0.1:18791
```

The native bot's optional **✨ Mini App-ı aç** button is shown only if
`MINIAPP_ENABLED=true` and a configured HTTPS URL exists. Set the
approved domain and Mini App URL using official Telegram bot management
before exposing it. Keep the bot native dashboard as operational fallback.
The web client refuses unsigned ordinary-browser access. It contains no
`initDataUnsafe` use or client-side privilege checks as an authority.

**Mini App document upload (single file, explicit consent):**
Select **Sənəd əlavə et**, choose one PDF/DOCX/XLSX/TXT/JPG/PNG
(max **8 MiB**), and choose **Faylı yoxla**. The server streams
bytes into a random private `staged/` path and returns a short-lived
10-minute preview token bound to the current verified ACTIVE actor.
At preview time **no FileObject, ingestion session or index job is created**.
Only **Təsdiqlə və saxla** invokes the existing IngestionSessionService's
`add_file` and `commit` boundaries. A successful response means
**queued for ingestion**, not OCR/indexed/completed. **Ləğv et**
removes the uncommitted temporary file. One file per confirmation.

The service refuses to auto-save if Telegram already has an open
ingestion session. Even if a second upload appears while the web file
is being approved, `commit` now validates the expected session ID,
file count and instruction count **inside the locked transaction**:
it may fail closed and require the operator to use native Telegram
`/saxla` or `/cancel` to resolve the pending session; it must never
commit someone else's new pending files without confirmation.
The native `/saxla` method's default behavior remains unchanged.
The temporary preview lives in memory, so a restart invalidates its
approval token. Operators should inspect temporary storage for
orphaned unconfirmed files during restart/cleanup and must never
blindly erase staged files referenced by the ingestion database.
This increment does **not** add web sales posting, stock mutations,
picture-relation confirmation or bulk import.

**Document processing progress (read-only):** the `Sənəd əlavə et`
screen now includes `Göndərdiyim sənədlər` and an explicit
`Vəziyyəti yenilə` control. The signed `POST /api/uploads/recent`
lists up to six of the current ACTIVE actor's latest committed ingestion
jobs. `POST /api/uploads/status` looks up one job ID within **that same
actor's ownership**; an unknown or another employee's ID is returned
as 404 with no document metadata. The view never reads a storage path
or reveals internal exception text.

Status is derived exclusively from database records:
`queued` = ingestion pending, `reading` = worker parsing/OCR,
`indexing` = parsing succeeded but searchable index is still pending,
`ready` = all parse outputs succeeded and the index job succeeded with
a positive indexed chunk record **for every parsed item**,
`review` = deferred/partial/zero-searchable-text needing attention, and
`failed` = ingest or indexing failure. The UI does **not** invent
percentage completion or infer that a committed upload is already
searchable. Manual refresh avoids excessive traffic and keeps the AI
request rate limit available for actual questions; the screen also
recovers these durable jobs after a browser restart without client
storage or previously remembered approval tokens.

**Before real activation:** confirm both processes see the same database,
bot token and configured evidence projection; test Telegram iOS/Android/web
launch; verify HTTPS/TLS, browser CSP, source text encoding, known zero/unknown
stock, expired/replayed/tampered initData, revoked user, concurrent questions,
timeouts, DB/index/AI outages and a rollback with `MINIAPP_ENABLED=false`.
The web UI has not yet been manually tested in a real Telegram client or
deployed to the operator's WSL host from ChatGPT. This increment does not
resolve the broader acceptance criteria of issue #43 (authenticated
document delivery/upload and expanded screens).

Official Telegram guide: https://core.telegram.org/bots/webapps

## Conversation-first Telegram interface (native bot)

This release introduces one home screen for an ordinary office employee:
**Sual ver**, **Bizim baza**, **Məhsul**, **Anbar qalığı**,
**Sənəd əlavə et**, **Satış**, **İnternet** and **Kömək**. Asking a
question does **not** require selecting a mode or learning slash commands.
The default conversation already uses internal business facts and
retrieved document evidence when configured; selecting **Bizim baza**
forces internal-only grounded retrieval. In this system, "baza" means
the company's structured data **plus** indexed PDF/DOCX/XLSX/TXT,
notes, image metadata and any available internal evidence—not simply
the relational database. Explicit internet search is separate and
warns users not to share confidential content.

The small help screen explains the first action in simple Azerbaijani
and offers examples, with `/help` retained for advanced options.
Grounded answers attach **Yeni sual**, **Bizim baza** and **Menyu**
buttons to their *last* Telegram message only. Those buttons create
a **new** navigation message rather than editing the existing answer:
the cited source identifiers and prior message remain available for
document requests and auditing. Answer buttons re-check the Telegram
actor's active account, and reset an older internet-only choice before
starting another message. No button writes product, stock, sales or
documents without that workflow's distinct confirmation.

For answers that have an original file-backed citation, the answer footer
also offers up to three **📎 Mənbə N** download buttons. These do not
send a guessed file or a catalog screenshot: the existing source-delivery
service independently verifies the current ACTIVE user, the latest eligible
persisted conversation source, the file's active state and delivery limits.
A short-lived per-user button token is tied to both the citation number
**and the original file ID**. If another answer changes the latest cited
file, the old button becomes invalid rather than sending the wrong
document. New question and web-search turns invalidate older buttons.
Downloads open the committed original bytes only on user tap, and leave
the visible answer unmodified. Files cannot be delivered from a group chat.
For more than three originals, users may still write "2-ci mənbəni göndər".

These are Telegram-native inline controls, not yet a standalone
Telegram Web App/Mini App. A full web surface requires a separately
authenticated web backend, Telegram signed `initData` verification,
TLS origin/hosting and staging acceptance before it can safely be
enabled. Never treat Telegram callback data as authorization.
After staging deployment verify the chat flow with novice users,
long split answers with sources, stale buttons, disabled accounts,
old internet-search modes and touch navigation on Android and iOS.

## Explicit inventory ledger and stock accounting (migration 0020)

Stock is not inferred from catalogs, price offers, sales invoices, customer
payments, product photos, PDF text, or AI predictions. `inventory_balances`
holds one **known** nonnegative quantity per active product and named
warehouse. If a product has no approved opening balance, `/stok KA45` and
`Stok: KA45` explicitly report **UNKNOWN**, not zero. This protects against
misrepresenting missing warehouse records as actual availability.

The touch-first `🏬 Anbar qalığı` menu now accepts a product code and
shows the known per-warehouse quantities without running the document model.
Clearly phrased code questions such as `KA45 stokda varmı?` and
`KA45 neçə ədəd qalıb?` also resolve deterministically to this ledger,
while ambiguous questions about products lacking an identifiable code
continue through normal document/business evidence. This does not mean
that a catalog/price listing constitutes available warehouse stock.

The authorized, private Telegram UI uses a two-step confirmation:

```text
/stok KA45
Stok: KA45
Anbar əməliyyatı: KA45 | Əsas | AÇILIŞ | 10.000 | SAY-2026-001
Anbar əməliyyatı: KA45 | Əsas | GİRİŞ | 5.250 | QE-2026-001
Anbar əməliyyatı: KA45 | Əsas | ÇIXIŞ | 2.000 | CI-2026-001
```

`AÇILIŞ` is the operator's first witnessed warehouse count. An existing
warehouse cannot be opened a second time, and a new warehouse cannot have
`GİRİŞ` or `ÇIXIŞ` before its opening count is recorded. Zero opening is
valid and means a *known* empty warehouse. Only administrators can post
movements, and the user must click the confirmation button before a write.
The approval expires, is removed after use, and re-checks admin privileges.
A globally unique **product/warehouse/reference** constraint rejects
replayed approvals. Nonnegative balances are enforced at both service and
database levels, and all successful entries append audit events with source
document reference, operator and resulting balance. Product-row locking
serializes concurrent first openings and concurrent withdrawals.

The three operations are manual warehouse events, **not** automatically
inferred from financial sales. An admin should enter only inventory changes
verified from an actual stock count or goods receipt/dispatch document.
There is deliberately no unsourced arbitrary `set stock to N` overwrite:
an incorrect opening or issue requires a separately designed, audited
correction procedure, not deletion or silent historical rewriting. This
first increment does not cover inter-warehouse transfers, reservations,
lot/batch expiry, returns, picking or multi-tenant branch ownership.

Run Alembic migration `0020_inventory_ledger` on a backed-up staging
copy, check `alembic_version`, verify the above paths with at least two
authorized roles, duplicated reference numbers and concurrent withdrawals,
then test actual Telegram confirmation/replay and restore. **No production
stock opening or migration has been run from ChatGPT.**

## Historical scanned-PDF OCR replay (operator controlled)

This is **different** from `repair_legacy_index`: a historical **fully scanned**
PDF had no normalized searchable text, so its original bytes require a new
parser attempt. The durable `ocr_replay_jobs` table records source and result
parse IDs; original `file_objects`, `ingestion_session_items`, Telegram
message identity, and old parse runs remain unchanged. No business extraction,
sales or user-message submission is replayed.

Apply the new Alembic migration **0019** as part of a reviewed deployment.
After verifying a PostgreSQL backup, Qdrant snapshot, storage backup, OCR
language packs and worker health, inspect the old candidates inside the app
container (default **read-only DB** mode):

```bash
sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T app uv run --no-dev python -m office_assistant.scripts.replay_scanned_pdfs --limit 100
```

Review the bounded `inspected`, `eligible`, `queued`, `skipped`
counts first. This only considers the **latest** historical parse per physical
active PDF: a deferred OCR-required result with valid, zero-chunk normalized
data, original session-item provenance, and no successful indexed version.
Unreadable/missing raw sources are not made searchable.

Only in an approved maintenance window, run the **explicitly mutating**
variant:

```bash
sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T app uv run --no-dev python -m office_assistant.scripts.replay_scanned_pdfs --apply --limit 100
```

`--apply` queues work transactionally; it **does not perform OCR in the CLI**
and it **does not certify indexing as completed**. The worker claims one
replay at a time using expiring leases, validates original PDF bytes against
their stored SHA-256, extracts OCR locally, creates a **new** normalized parse
run, and schedules the existing Qdrant index job. Replay deduplication is
per physical file; rerunning `--apply` does not create duplicate work.
Failures remain visible in `ocr_replay_jobs.status/last_error` and
`knowledge_index_jobs`, without logging document text. A blank scan remains
unsearchable.

The readiness doctor's `historical_pdf_ocr_replay` result now reports:
`pending_or_running`, `failed`, `awaiting_index`, and `indexed`.
Only a successful knowledge index **record with more than zero chunks**
counts as indexed. `INDEX_QUEUED` without confirmed nonzero index records
keeps readiness at `DEGRADED`; a partially successful index job is also
reported as degraded. Thus a queued or failed replay cannot masquerade as a
fully searchable corpus.

After workers finish, rerun the readiness doctor and inspect the index
completion event. Search for a unique phrase in each representative recovered
PDF, confirm the original PDF page number and source delivery, and verify that
a second unrelated file in the same original session remains indexed. Do not
report full document recovery based only on replay status
`INDEX_QUEUED`: Qdrant indexing can still fail. No production replay has
been executed as part of this change.

## Supervised retries after OCR or Qdrant failure

**Never run these against live data without a verified backup and staging
acceptance.** An operator must choose exactly one phase; both commands default to
a read-only PostgreSQL inspection, with bounded counts and no document text in
the CLI output:

```bash
# OCR failed and no successful OCR-generated parse exists
sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T app uv run --no-dev python -m office_assistant.scripts.retry_scanned_pdfs --retry-failed-ocr --limit 100

# OCR succeeded but its Qdrant index projection did not
sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T app uv run --no-dev python -m office_assistant.scripts.retry_scanned_pdfs --retry-failed-index --limit 100
```

After reviewing `inspected`, `eligible`, `queued`, and `skipped`,
repeat the selected command with **`--apply`** only in a controlled window.
Do not use both phase flags together. Neither option resets raw uploaded
documents, creates synthetic Telegram submissions, nor replays business
extraction or sales. Both first re-check the source's original SHA-256 digest,
its session provenance and absence of another searchable version.

An OCR-phase retry is limited to terminal `FAILED` replay rows with no OCR
parse result, and creates an explicit new worker lease. An index-phase retry
reuses the already successful parsed text and original `parse_run_id`, without
calling OCR. Only a terminal `FAILED`, `PARTIAL`, or inconsistent
`SUCCEEDED` index job for which this exact parse is **not** successfully
indexed with nonzero chunks may be requeued; other successfully indexed
files' records are not changed. Concurrent operator attempts are serialized
using database row locks. Index vector IDs remain deterministic for the
original parse run; an index retry therefore upserts rather than inventing a
new source identity.

Every applied retry appends an `OCR_REPLAY_OPERATOR_RETRY` audit event
with its phase and previous attempt counter. Document content, private file
paths and raw OCR failures are not included. Terminal `SKIPPED` jobs, missing
or mutated original PDF bytes, and already indexed files are not eligible.
A queued retry is **not** a completed repair. Verify a nonzero
`KnowledgeIndexRecord`, the `historical_pdf_ocr_replay` readiness check,
original page locator, source delivery and representative Telegram searches.
If readiness remains `DEGRADED`, diagnose the failed phase before another
operator intervention.

## Scanned PDF OCR (new imports)

The ingestion **worker** can now recognize page images locally using Poppler and
Tesseract. The image stays on the server; no page pixels or OCR text are sent to an
external OCR provider. A PDF's native text pages remain untouched; scanned pages
are added as `page_ocr` blocks with original PDF page numbers. Both kinds of
blocks use the existing Qdrant index queue.

The read-only readiness probe now includes a `pdf_ocr` check. It verifies
that the renderer executes and Tesseract reports the configured language packs;
missing prerequisites yield `DEGRADED`, not a misleading `READY`.

The container image installs `pdftoppm`, `tesseract` and default language packs
for Azerbaijani, English and Russian. The worker enables OCR by default.
Set `PDF_OCR_ENABLED=false` only for an explicit temporary rollback.
Optional worker settings: `PDF_OCR_LANGUAGES` (default `aze+eng+rus`),
`PDF_OCR_MAX_PAGES` (80), `PDF_OCR_PAGE_TIMEOUT_SECONDS` (15),
`PDF_OCR_DOCUMENT_TIMEOUT_SECONDS` (150). Limits are per document;
pages left unreadable or outside these limits remain visibly marked as requiring OCR.

After building/deploying the matching commit, verify the binaries inside the **worker**:

```bash
sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T worker tesseract --list-langs
sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T worker pdftoppm -v
```

Submit a known scanned, text-bearing PDF as a **new import**. Confirm that its
index-completion notification reports nonzero chunks, that searching for a unique
phrase finds the document, and that the result points to its correct PDF page.
A genuinely blank image cannot produce searchable chunks and remains deferred.
Run mixed native+scanned, multi-page and poor-quality document acceptance cases.

**Important:** `repair_legacy_index` only requeues pre-existing normalized
*text*; it does not OCR previously deferred image-only PDFs. For those files
use the historical `replay_scanned_pdfs` dry-run and reviewed `--apply`
workflow above. If OCR or Qdrant later fails, use the phase-specific
`retry_scanned_pdfs` commands. None of these operations has been executed
against the user's live WSL data as part of these GitHub changes.

## Opt-in multi-angle product image candidates (experimental)

The image-asset and product-confirmation tables already protect exact identity.
Simple dHash/aHash near-duplicates remain available with the feature **off**.
Opt-in local semantic retrieval adds a second, *unverified candidate-only* tier
to photo questions when no conservative visual duplicate was found. It does
not create products, update a relation, infer price or make stock claims.

The implementation uses FastEmbed's local-only CLIP vision encoder
(`Qdrant/clip-ViT-B-32-vision`, 512 dimensions); its constructor sets
`local_files_only=True` so a Telegram photo does **not** trigger a Hugging Face
download or vendor API call. The model (~340 MB) must be pre-provisioned in
the **app** container's persistent `/models/cache` by an operator on an
approved network and checked against the expected artifact/revision/license
before enabling. Example *operator-initiated* provisioning (not used by the
chat bot or ingestion worker):

```bash
sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T app uv run --no-dev python -c 'from fastembed import ImageEmbedding; ImageEmbedding(model_name="Qdrant/clip-ViT-B-32-vision", cache_dir="/models/cache")'
```

After provisioning the local model, prepare the already human-confirmed
reference photos **before** enabling Telegram semantic lookup. Preview is read-only
and displays counts only (no filenames, product codes, embeddings or photo data):

```bash
sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.local.yml exec -T app uv run --no-dev python -m office_assistant.scripts.prepare_visual_references --limit 80
```

In a planned staging maintenance session, append `--apply` to calculate and
persist only approved reference vectors. The command verifies active relations
and source image SHA-256, limits input resolution and CPU threads, reports
`MODEL_UNAVAILABLE` when local model assets are missing, and is idempotent by
content digest and model version. An applied prewarm never uploads a picture,
writes a product relation or caches the employee's query photos. Reference
revocations immediately disappear from subsequent active-reference lookups.

Set `VISUAL_SEMANTIC_ENABLED=true` only in a controlled staging rollout.
`VISUAL_SEMANTIC_MODEL_VERSION=clip-vit-b32-v1` labels the operator-reviewed
model snapshot and also namespaces the local cache; change it on model or
preprocessing upgrades. `VISUAL_SEMANTIC_MIN_SCORE=0.60` is a conservative
**display threshold, not a calibrated probability**, and should be tuned on
a separate holdout set of real customer photos before production use.
`VISUAL_SEMANTIC_MAX_REFERENCES=80` bounds per-request CPU work. The
Telegram handler has a 25-second deadline and detects a busy model; the
regular chatbot stays available if the optional model is unavailable.

Only currently active, explicit user-confirmed reference images are read,
up to the bounded latest-reference subset. Every source image's bytes are
checked against its stored SHA-256, paths are constrained inside storage,
and EXIF orientation and input pixels are bounded. Per-reference embeddings
are cached locally by content hash and model version, **never the unconfirmed
query-photo embedding**. Revoked references drop out of the database query
immediately even if a stale cache file still exists. Purge obsolete model
versions and reference cache material during scheduled retention maintenance.
This repository currently operates as one company tenant; any future
multi-tenant rollout must add a tenant predicate and isolation tests first.

Accept only after running `benchmark_visual.py` with labeled **different
camera angles, background changes, low light, similar models and unseen
products**, and documenting recall, false-identity rate and abstention.
No semantic score is a confirmation or a warehouse-stock assertion. Where
no adequately similar candidate exists, the bot abstains; a returned
candidate still requires human code confirmation before it can become a
product fact. The exact/near-duplicate path remains the safe fallback while
this flag is disabled.

The readiness probe does not constitute production certification: manual bot interaction, cross-user access control, resource and soak tests, restore drills and customer document accuracy tests remain necessary.
