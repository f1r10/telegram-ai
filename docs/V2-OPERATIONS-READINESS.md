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

After workers finish, rerun the readiness doctor and inspect the index
completion event. Search for a unique phrase in each representative recovered
PDF, confirm the original PDF page number and source delivery, and verify that
a second unrelated file in the same original session remains indexed. Do not
report full document recovery based only on replay status
`INDEX_QUEUED`: Qdrant indexing can still fail. No production replay has
been executed as part of this change.

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

**Important:** the historical index-repair command above only requeues pre-existing
normalized *text*. It does not run OCR on previously deferred image-only PDFs.
Those older scans must be safely reprocessed through the ingestion pipeline
after backup and deduplication checks, not simply reindexed. This PR does not
automatically reprocess existing documents or mutate old production records.

The readiness probe does not constitute production certification: manual bot interaction, cross-user access control, resource and soak tests, restore drills and customer document accuracy tests remain necessary.
