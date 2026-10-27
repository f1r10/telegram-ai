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
`DINO_STAGING_ENABLED=true` test with the compose overlay. The default is
`false` even if an old `VISUAL_SEMANTIC_ENABLED=true` remains in `.env`.
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

A future dedicated Qdrant ANN visual index is still open. Current bounded
CPU scoring verifies on-disk source digests and compares at most 500 active
references per source type; this procedure does not satisfy unlimited
source coverage or a measured 5-second end-to-end p95.

### All embedded document image types (2026-10-27)

Current `prepare_visual_catalog` output labels active embedded image counts
as `documents=` (PDF + DOCX + XLSX), replacing the older PDF-only `pdf=`
field. The normal ingestion worker registers image assets from all these
formats. When the optional visual CPU overlay is used, **both app and worker**
receive `DINO_STAGING_ENABLED:-false` and the same versioned DINO model cache;
with the flag explicitly turned on, the worker automatically computes at most
100 new embeddings after committing each uploaded file. If a document is
stored while the model is OFF, run the existing operator backfill `--apply`
before declaring its vectors prepared. Upload-time prewarm is best effort;
search may lazily compute missing vectors and report unavailable if the offline
model is not installed. This does NOT auto-approve a photo as a product SKU.
