# DINOv2 CPU staging runtime — isolated operator procedure

Status: code and CI candidates only; this document does not claim any Kali
staging deployment, owner phone-photo calibration, or production readiness.
The original `Dockerfile`, Telegram bot, and PostgreSQL/Qdrant volumes remain
unchanged unless the operator explicitly uses the optional files.

## 1. Review the existing staging environment

Use `~/telegram-ai-staging` with an existing approved backup/snapshot and a
clean `main` checkout. Follow `docs/PHOTO-STAGING-MANUAL-GATE-2026-10-26.md`
first. Do not remove volumes, copy product data into the independent
`local-image-search` SQLite database, upload customer photos to GitHub or
modify the production bot.

## 2. Prepare DINOv2 while the existing bot stays running

Build only the optional CPU operations image. This does not recreate the
existing `tgstage_app` or enable the new encoder.

```bash
cd ~/telegram-ai-staging
git pull --ff-only origin main
docker compose -f docker-compose.staging.yml -f docker-compose.visual-cpu.yml --env-file .env config --quiet
docker compose -f docker-compose.staging.yml -f docker-compose.visual-cpu.yml --env-file .env build visual-cpu-ops
```

The next command is the one and only **explicit, authorized** model-weight
download. It accesses Hugging Face for the *public DINOv2 Small model*; never
submit product images. It saves processor and weights to the preexisting shared
`tgstage_model_cache` volume, not to Git. The normal Telegram inference
uses `local_files_only=True`.

```bash
docker compose -f docker-compose.staging.yml -f docker-compose.visual-cpu.yml --env-file .env run --rm visual-cpu-ops \
  uv run --no-sync --no-dev python -m office_assistant.scripts.prepare_dino_runtime --download-model
```

Expected diagnostic:
`DINO_RUNTIME=READY dimension=384` and
`DINO_PROBE=ONLY_SYNTHETIC_IMAGE`. Failure is `DINO_RUNTIME=UNAVAILABLE`;
do not enable semantic matching. Real-world phone accuracy is **not** tested by
this synthetic check.

Next inspect real indexed source counts **without performing inference**:

```bash
docker compose -f docker-compose.staging.yml -f docker-compose.visual-cpu.yml --env-file .env run --rm visual-cpu-ops \
  uv run --no-sync --no-dev python -m office_assistant.scripts.prepare_visual_catalog --limit 500
```

Check aggregate `approved`, `pdf`, `truncated`. If no approved active
image relationships exist, register them through Telegram's explicitly
confirmed catalog process before benchmarking. If any subset is truncated,
do not claim complete catalog coverage.

Once counts and source permissions have been reviewed, cache only the
approved/PDF source vectors locally; **no query photographs are persisted**:

```bash
docker compose -f docker-compose.staging.yml -f docker-compose.visual-cpu.yml --env-file .env run --rm visual-cpu-ops \
  uv run --no-sync --no-dev python -m office_assistant.scripts.prepare_visual_catalog --apply --limit 500
```

Expect `CATALOG_VISUAL_PREWARM=APPLIED`, with
`unavailable=false` inferred from the non-UNAVAILABLE status; inspect
`prepared`, `skipped` and `truncated`. A partial or empty set is not a
pass. **Never run the old `activate_catalog_visual_staging.sh` shortcut**;
it enables a different CLIP pathway and downloads weights automatically.

## 3. Holdout benchmark before enabling Telegram

Create your own private labelled test set as described in
`docs/V2-VISUAL-PHOTO-ACCEPTANCE.md`. Distinguish identical, phone
side/back angles, hard lookalikes and unrelated negative objects. On Kali,
using the already mounted model and private, read-only holdout directory,
run offline evaluation from the opt-in operations image. Example private
path `/srv/office/visual-acceptance`, to be replaced by the actual path:

```bash
docker compose -f docker-compose.staging.yml -f docker-compose.visual-cpu.yml --env-file .env run --rm \
  -v /srv/office/visual-acceptance:/private/visual-acceptance:ro \
  visual-cpu-ops uv run --no-sync --no-dev python -m office_assistant.scripts.evaluate_visual_photos \
  --manifest /private/visual-acceptance/truth.jsonl \
  --photo-root /private/visual-acceptance/photos --apply --limit 500
```

The evaluation prints aggregates only. Compare recall@1 and @3,
`negative_candidate_rate`, `false_positive_rate`, `truncated` and
warm p95. DINOv2 cosine scores are **not probabilities**; the CLIP-derived
0.60 threshold must be calibrated on held-out negatives and never treated as
proof of a SKU or stock.

Only after quality review, operators may plan an explicit staging
`DINO_STAGING_ENABLED=true` test with the compose overlay. The older overlay default was
`false`; as of 2026-10-28 the dedicated CPU overlay defaults to
`DINO_STAGING_ENABLED=true`. The base Compose image still sets visual
inference OFF and lacks Torch packages. An authorized operator must
explicitly use the CPU overlay and provision weights first.
**No automated code path here flips the switch or restarts the app.**
The standard `worker` remains on its unchanged base Dockerfile. No
YOLO/Ultralytics dependency has been introduced.

## 4. Rollback and observed limits

To remove the optional CPU app after a separately approved test, use
the previous normal staging Compose build of `app` from the recorded
previously approved commit; do not remove/reinitialize databases, model
cache, or persistent Docker volumes. Preserve the current `.env` and owner
backups. If a real product angle is missed or an unrelated candidate appears,
record it as a failed recognition case; prefer an honest abstention to an
invented authoritative match.

Historical note (superseded 2026-10-28): the former on-process matcher
scored at most 500 references. The separate Qdrant DINO ANN now supports
full-corpus keyset backfill and verified bounded top-K candidates. That
does **not** establish measured five-second mobile end-to-end p95.

### All embedded document image types (2026-10-27)

Current `prepare_visual_catalog` output labels active embedded image counts
as `documents=` (PDF + DOCX + XLSX), replacing the older PDF-only `pdf=`
field. The normal ingestion worker registers image assets from all these
formats. When the optional visual CPU overlay is used, **both app and worker**
receive `DINO_STAGING_ENABLED:-false` and the same versioned DINO model cache;
with the flag enabled, the worker traverses all new registered image
occurrences in bounded keyset batches of 64 after each committed file; failures
remain visible and require reconciliation. If a document is
stored while the model is OFF, run the existing operator backfill `--apply`
before declaring its vectors prepared. Upload-time prewarm is best effort;
search may lazily compute missing vectors and report unavailable if the offline
model is not installed. This does NOT auto-approve a photo as a product SKU.

## Full-corpus DINOv2 ANN and standard model (2026-10-28)

DINOv2 Small (`dinov2-small-mean-v1`) is the new **application default**
for offline visual candidates. The dedicated CPU Compose overlay also enables
the model by default. The ordinary lean Docker image does *not* bundle CPU
Torch weights; use the reviewed `Dockerfile.visual-cpu` overlay and provision
public model weights **once** via the existing operator-only
`prepare_dino_runtime --download-model`. If model weights are missing,
visual matching abstains and office/stock/document features continue working.

The previous 500-image process scan is **legacy fallback only** while the full
visual index has not been built. The new operator-only backfill streams
PostgreSQL's entire set of ACTIVE file images and USER_EXPLICIT confirmed
product picture relations using 64-record keyset batches; it has **no global
500- or 10,000-image corpus limit**. It writes a separate
`office_visual_dino_v1` Qdrant collection with cosine 384-vectors. This is
not the existing document RAG collection. It sets a persistent
`office_visual_dino_ann_ready_v1=ready` marker only after fully successful
indexing with no skipped source. That marker is set to `building` before any
write, so incomplete indexes do not claim full coverage.

On Kali staging, after verifying the existing backups and preprovisioning
DINO weights (previous sections), use the optional visual CPU image to
dry-run the complete corpus count (no data write):

```bash
docker compose -f docker-compose.staging.yml -f docker-compose.visual-cpu.yml --env-file .env run --rm visual-cpu-ops \
  uv run --no-sync --no-dev python -m office_assistant.scripts.rebuild_visual_ann
```

After reviewing active source counts, run the controlled ANN backfill:

```bash
docker compose -f docker-compose.staging.yml -f docker-compose.visual-cpu.yml --env-file .env run --rm visual-cpu-ops \
  uv run --no-sync --no-dev python -m office_assistant.scripts.rebuild_visual_ann --apply --batch-size 64
```

Require output `VISUAL_ANN=APPLIED ... skipped=0 complete=true` before
claiming full indexed coverage. A skipped file, missing weights or unavailable
Qdrant is a FAILED acceptance gate. Query ANN returns at most 40 scored
candidates for display, but its **search universe** covers every indexed
ACTIVE source regardless of total archive size. Product candidate IDs are
validated against current USER_EXPLICIT relations; document candidates
against current ACTIVE original file plus SHA-256 bytes, on every query.

A new upload's post-commit worker can incrementally upsert source and
approved-product vectors to this index. It is a derived, asynchronously updated
projection, **not** the authoritative data store. Re-run the full backfill
after operator changes/deletes/revocations, a worker indexing failure, or a
large historical import, before asserting complete coverage again. Old Qdrant
points are never trusted after their parent record is deleted or loses active
status: DB and content hash verification happen at query time.

**Operational caveat:** The user explicitly requested DINO by default, but
this does not magically provision weights, install the Kali image or validate
real cross-angle recall. Full CPU deployment, index freshness metrics and
real-phone false-positive / p95 acceptance remain to be verified on the actual
Kali staging VM. Do not expose the model to production before these tests.

## V1 derived-index reconciliation (2026-10-28)

On `rebuild_visual_ann --apply`, the full active PostgreSQL corpus is
re-indexed in bounded batches **before** the readiness flag is set. A final
Qdrant scroll reconciles old visual points against current ACTIVE files and
human-confirmed product relations, deletes revoked/superseded/changed-digest
points from the **derived visual collection only**, and emits aggregate
`VISUAL_ANN_RECONCILE inspected=N pruned=M`. Original uploads, business
facts and text RAG indexes are untouched. If indexing or reconciliation
fails, the index remains in `building` state: the bot must not claim it
searched every active image. This is not a background maintenance service;
repeat after a large import or authority change before final UAT.

Search may overfetch up to 120 ANN vector candidates to recover up to 40
currently valid results after revocation filtering. This is a **candidate
window**, not an archive-size cap. Every shown result retains ACTIVE
PostgreSQL/SHA source checks; a real-world p95 performance claim still
requires an owner-supplied Kali benchmark.
