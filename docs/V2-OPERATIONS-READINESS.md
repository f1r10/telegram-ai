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
