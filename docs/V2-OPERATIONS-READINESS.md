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
