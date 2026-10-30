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


## V1 photo-caption question acceptance (added 2026-10-30)

These checks are separate from the five photo-recognition acceptance cases.
They do not authorize activating DINOv2 on uncalibrated private photos.

1. Attach an *exact active confirmed reference* with the caption
   `Bu məhsulun qiyməti nə qədərdir?`; select `Bazada axtar`.
   The source match is exact only for the identical SHA-256 file, and any
   price must come from a cited internal fact for that SKU. An absent or
   unavailable source yields no invented price, sale or warehouse quantity.
   The caption must survive the Telegram photo-choice callback.
2. If calibrated and explicitly enabled ANN returns only a **similar**
   candidate, a product-code question button may be shown. Tapping it is
   a human-selected *query for that code*, not confirmation that the
   photo depicts that SKU. The bot re-downloads the photo, verifies the
   currently active candidate ranking and its code checksum, then may
   answer with internal citations. Any stale/revoked/reordered candidate
   is refused, and photo ownership remains unconfirmed.
3. Multiple images in one Telegram album keep their own captions.
   A question for the second image must not be answered with the first
   image's source or selected code. No query photo is permanently
   stored by this read-only path; saving a reference still requires the
   separate `/saxla` boundary.
4. Recheck original source-file delivery, no unapproved external image
   egress, and no business write or product-image relation created by
   selecting a visual candidate.

These code paths have synthetic CI coverage. Real operator-confirmed
accuracy, p95 latency, genuine private catalog facts and Kali staging
deployment remain **unverified until executed on that host**.


## Persistent V1 photo groups: staging schema prerequisite (2026-10-30)

PR #147 adds `0023_photo_groups` as an **additive** migration. It does not
delete or rewrite any existing file, stock row, sale, product photo approval,
or Qdrant point. The album save and group management commands require the
new tables to exist. `scripts/deploy_photo_staging.sh --deploy` therefore
reads `alembic_version` in the **staging** PostgreSQL service before it
recreates either app or worker. Missing revision reports
`PHOTO_STAGING=BLOCKED reason=schema_migration_required`. No code-only
rollout should proceed against the old schema.

Only after a current verified **staging** database backup and a reviewed
rollback plan, the operator can apply the additive schema migration on the
staging Docker project with the newly built code, without enabling DINO,
starting another Telegram poller, or touching legacy Docker volumes:

```bash
cd ~/telegram-ai-staging
git status --short --untracked-files=no
git branch --show-current
git pull --ff-only origin main
docker compose -f docker-compose.staging.yml --env-file .env build app worker
docker compose -f docker-compose.staging.yml --env-file .env run --rm --no-deps app \
  uv run --no-dev alembic current
# Review the existing staging backup and rollback checkpoint BEFORE this step.
docker compose -f docker-compose.staging.yml --env-file .env run --rm --no-deps app \
  uv run --no-dev alembic upgrade head
bash scripts/deploy_photo_staging.sh --deploy
```

No command above has been executed on Kali by GitHub CI. A successful
disposable database migration is not proof of a staging backup, real model
accuracy, or a stable Telegram session. A failed preflight must remain blocked
rather than silently deploy incompatible code.

### Group lifecycle acceptance

In an existing authorized test account only:

1. Save an album with `Ad:` metadata and verify that one group ID is returned.
   `/qruplar` shows its count; a second account cannot see it.
2. `/qrup_redakte <ID> | Ad: Yeni ad` changes metadata revision without
   changing the original image files, product confirmation, stock or price.
3. `/qrupa_elave <ID>` followed by a new photo creates only a *pending*
   ingestion file. `/saxla` makes its group membership durable and increments
   the count. `/cancel` discards pending membership. Duplicate content,
   expired pending selections and cross-user group IDs must fail closed.
4. The existing text index may retain an older descriptive upload note after
   metadata edit; treat new group metadata as authoritative **for group
   management only** until a versioned textual reindex mechanism is verified.
   Never report a changed group description as an approved product fact.
