# Kali Telegram photo staging: safe deployment and manual acceptance

Date: 2026-10-26. Scope: the existing **Kali staging only**
(`~/telegram-ai-staging`, `tgstage_*`, `docker-compose.staging.yml`).
This procedure does NOT authorize production rollout or enable visual
semantic inference. The operator's approved current backup and rollback
commit must be known before replacing running containers.

## Operator deploy: code only

**Prerequisites:** staging bot/worker are already running, persistent backups
have been checked, Git working tree is clean, `.env` is staging-only,
and no other machine polls the same test Telegram bot token. No screenshots
of credentials, original customer files, or private photo datasets in GitHub.

From the Kali terminal:

```bash
cd ~/telegram-ai-staging
git status --short --untracked-files=no
git branch --show-current
git pull --ff-only origin main
bash scripts/deploy_photo_staging.sh --check
bash scripts/deploy_photo_staging.sh --deploy
```

If the tree is dirty, the branch is not `main`, or the check fails, **stop**;
do not reset or overwrite local work. The staging rollout script verifies
the Compose project identity, builds before restart, recreates only existing
`app` and `worker`, and runs read-only readiness, approved-reference counts,
and the catalog image dry run. It does **not** run migrations, modify
`.env`, download CLIP, enable semantic inference, create warehouse entries,
or delete Docker volumes. Do **not** run
`scripts/activate_catalog_visual_staging.sh` for this initial acceptance:
it changes feature flags and downloads/activates the model.

Success requires `PHOTO_STAGING=DEPLOYED`, a stable app/worker, plus reviewed
`check_readiness` and preflight output. `PHOTO_STAGING=DEPLOYED` is **not**
a declaration of photo-recognition success. Record the previously running
commit shown in output for code-only rollback under the delivery runbook.

## Manual Telegram checks — five core scenarios

Use **one existing, active, known product code**, a phone reference image,
and a separate unrelated object. Never create price or stock from the photo.

1. **Confirmation.** Send a photo with `Bu KA45-dir, yadda saxla` (replace
   KA45 with the actual existing SKU). Complete explicit `/saxla` or the
   `Yadda saxla` confirmation. Re-run aggregate
   `check_three_acceptance_gates`. The number of active confirmed photo
   relations must increase; a staged or unconfirmed uploaded file is not
   a product reference. If the product is absent, create/approve it
   through the authorized catalog workflow first; do not invent a SKU.
2. **Same photo.** Re-send the original photo from Telegram with
   `Bu məhsul bizdə varmı?`. Correct SKU and an exact confirmed result
   are expected only when the bytes are identical to the active reference.
   If Telegram changed compression, a near-duplicate *candidate* is
   acceptable but must not claim verified stock.
3. **Recompressed/cropped photo.** Same subject with changed size/compression
   or slight crop: evaluate possible candidates, never identity/price/stock.
4. **Different angle.** Independently capture the same real product from
   the side/back. With semantic inference still OFF, a safe
   no-result/candidate-only outcome may occur: record as **recognition gap**,
   not a passed cross-angle capability. Test offline semantic comparison only
   after private negative holdout calibration; do not enable on the strength
   of synthetic CI or one successful SKU.
5. **Unrelated object.** Submit a completely different product/object with
   `Bu məhsul bizdə varmı?`. It must not claim a confirmed SKU. Any
   unrelated suggested candidate is a **false-suggestion failure** requiring
   investigation; do not hide it behind zero identity-claim rate.

**PDF check.** Upload and explicitly save a permitted synthetic PDF whose
embedded product picture has a known source page. Submit a matching extracted
picture as a normal Telegram photo. If no approved SKU match exists, expect
only a PDF source-page *suggestion* with a correct original document, not an
approved company SKU or confirmed inventory. The quick fallback supports
near-duplicates, not general cross-angle PDF recognition.

**Timing.** Measure repeated end-to-end Telegram durations, separating
same photo, alternate angle, unrelated objects, PDF match, and no match.
Stage timeout limits are not overall response guarantees. Report median,
p95 when enough trials exist, timeouts, and any background model delay.

### Aggregate diagnostics (no filenames or credentials)

```bash
cd ~/telegram-ai-staging
docker compose -f docker-compose.staging.yml --env-file .env exec -T app \
  uv run --no-dev python -m office_assistant.scripts.check_three_acceptance_gates
docker compose -f docker-compose.staging.yml --env-file .env exec -T app \
  uv run --no-dev python -m office_assistant.scripts.prepare_visual_catalog
```

Expected `CATALOG_VISUAL_PREWARM=DRY_RUN` does not mean a visual model
is installed or calibrated; report `approved`, `pdf`, and `truncated`.
Do not use `--apply` on a new, unprovisioned model during the manual test.

## Failure handling

On a failed deploy or changed bot behavior, stop manual tests, preserve
persistent data and snapshots, note `PHOTO_STAGING=BLOCKED` reason,
sanitized service status, current and prior commit SHA. Follow the existing
runbook's **code-only rollback** after reviewing compatibility; do not
blindly reset Git, run `docker compose down -v`, restore the DB, or
enable unrestricted internet reverse-image search.

Photo acceptance issue #35 remains OPEN until independent real phone
holdout data establish reliable cross-angle recall, bounded false suggestions,
and measured latency, reviewed by the owner.
