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


## One-command read-only technical UAT gate — 2026-11-02

**This gate does not deploy code, migrate the database, create a backup,
activate semantic image matching, or run any owner-controlled mobile test.**
It is intended to run only on the existing Kali staging host *after* the
staging-only backup has been independently verified, migration
`0023_photo_groups` has been applied and the updated services have been
deployed. Never run the existing-stack preflight
`scripts/preflight_isolated_staging.sh` on an already-running staging stack:
that first-install probe deliberately treats existing staging volumes as a
collision.

Run from the existing staging checkout:

```bash
cd ~/telegram-ai-staging
git status --short --untracked-files=no
git pull --ff-only origin main
bash scripts/gate_manual_photo_uat.sh --full
```

`--full` requires a running staging bot, worker, PostgreSQL, Qdrant,
SearXNG and separately enabled Mini App with an HTTPS origin.
For **Telegram photo-only** acceptance, use
`bash scripts/gate_manual_photo_uat.sh --photo-only`;
this does *not* qualify the Mini App or the overall application for full
manual acceptance.

The gate checks the clean `main` checkout, untracked private `.env`,
staging Compose project and container health, active Telegram polling,
required database migration, actual readiness checks (including live
SearXNG JSON results and OCR prerequisites), aggregate photo reference
counts and visual-catalog dry run. It prints no credentials or customer
data. A successful read-only run ends with:

```text
MANUAL_UAT_GATE=TECHNICAL_PASS mode=--full sha=<actual-commit>
MANUAL_UAT=HUMAN_ACCEPTANCE_REQUIRED
MANUAL_UAT_WARN=REAL_PHOTO_ACCURACY_MOBILE_HTTPS_BACKUP_RESTORE_NOT_CERTIFIED
```

`TECHNICAL_PASS` **is not** a successful visual-model benchmark,
backup/restore certification, real HTTPS/TLS connection proof or Android/iOS
acceptance. A failed prerequisite returns
`MANUAL_UAT_GATE=BLOCKED reason=...` and a nonzero exit status.
Do not bypass a blocked gate or share the private `.env` file.

The existing staging deployment script performs code-only rollout and
requires migration 0023; it deliberately **does not perform an automatic
database migration** because an operator must first verify the backup,
restore checkpoint and schema compatibility. Follow the staged migration
procedure above if the gate reports `schema_migration_required`.


### When the Kali gate appears to hang after service health (2026-11-02)

Before PR #151, the gate printed five healthy service rows and then could remain
silent during Telegram polling, the schema query or the per-service read-only
probes (each of which had a 90-second Compose deadline). Seeing five rows
alone **never proved** the migration or photo runtime was ready; Ctrl+C is
an interrupted test, not a PASS or a confirmed deadlock.

The revised gate prints `MANUAL_UAT_CHECK=<stage> status=RUNNING` before
each blocking step and `status=PASS` only after it finishes. A timed-out
step fails closed with `reason=<stage>_timeout`. It also checks that both
the active app and worker images contain the V1 photo-group service: pulling
new repository files alone does not rebuild running containers.

After updating `main`, run the read-only test:

```bash
cd ~/telegram-ai-staging
git pull --ff-only origin main
bash scripts/gate_manual_photo_uat.sh --photo-only
```

On failure, report only the `MANUAL_UAT_CHECK` and `MANUAL_UAT_GATE`
lines. If `schema_migration_required`, follow the verified-backup and
staging migration procedure above. If `app_photo_group_runtime_failed`
or `worker_photo_group_runtime_failed`, the corresponding container may
still use an older code image: follow the *code-only* build and deployment
procedure, without changing storage volumes or bypassing the migration
prerequisite. Never share an unredacted Compose config, `.env`, tokens or
customer files. The gate cannot prove full commit/image parity, backup
restore or real-photo recognition accuracy.

### Resolving `schema_probe_failed` on Kali (2026-11-02)

The observed live result had five running/healthy staging services and
`MANUAL_UAT_CHECK=telegram_polling status=PASS`, but stopped at
`MANUAL_UAT_CHECK=schema_revision status=RUNNING` with
`reason=schema_probe_failed`. This does **not** prove that migration
0023 is missing: the SQL connection, version table or query may have failed.

The revised read-only gate separates the outcomes without printing PostgreSQL
credentials, database names, schema contents or raw server error messages:

- `postgres_sql_connection_failed`: the application-designated staging
  database could not complete a trivial `SELECT 1`; inspect that the
  container's `POSTGRES_DB` and `POSTGRES_USER` match the initialized
  local staging database, without publishing their values.
- `schema_version_table_missing`: database SQL works, but Alembic's
  `alembic_version` relation is absent in the active schema. Do not
  initialize or upgrade blindly; first verify this is the intended
  staging database and review its backup/restore point.
- `schema_migration_required`: the version table exists but its revision
  does not equal `0023_photo_groups`. Follow the previously documented
  staging-only backup + reviewed migration procedure.
- `schema_table_probe_failed` or `schema_revision_query_failed`:
  SQL is available but a further read-only metadata query failed.
  Investigate local PostgreSQL privileges/schema and do not bypass the gate.

After fetching the updated script, run
`bash scripts/gate_manual_photo_uat.sh --photo-only` and share only
the `MANUAL_UAT_CHECK` and `MANUAL_UAT_GATE` status lines. Keep the
existing database, image storage, Docker volumes, private backup and test
bot token untouched.

### PostgreSQL TCP authentication on the staging gate (2026-11-02)

The operator's direct read-only check succeeded with `psql -h 127.0.0.1`
and the container's `POSTGRES_PASSWORD`, while the gate's prior `psql`
call omitted `-h`. libpq therefore selected the local Unix socket, which
can use different PostgreSQL authentication rules. The gate now uses the
same loopback TCP/password-auth path, disables password prompts with
`-w`, and keeps all output suppressed. This does not change PostgreSQL
configuration or data.

The operator's `to_regclass` check returned `alembic_version` (unqualified),
not `public.alembic_version`; the gate already accepts both. The earlier
manual diagnostic printed the revision only when the result was the
qualified form, so it did not yet establish the actual revision. The updated
gate always queries `version_num` after confirming the table exists and
only passes when the revision is exactly `0023_photo_groups`.

After this code-only script update is merged, fetch `main` and rerun
`bash scripts/gate_manual_photo_uat.sh --photo-only`. The next result
will either confirm the expected revision or report a distinct, fail-closed
revision-query/migration reason. Do not run a migration until the exact
revision is known and the staging backup is verified.

### PostgreSQL authentication path (2026-11-07)

Both the read-only UAT gate and the code-only deployment script now query
the staging database over loopback TCP with the container's existing
`POSTGRES_PASSWORD`, using `psql -w` so no interactive password prompt can
hang a non-interactive run. This avoids relying on local Unix-socket
authentication. The probes remain read-only; they do not change roles,
passwords, schema or business data. The deploy script checks the exact
`0023_photo_groups` revision before rebuilding/recreating app and worker.

### Direct PostgreSQL container probe (2026-11-07)

The operator's direct `docker exec tgstage_postgres` TCP/password-auth check
succeeded, while the Compose service-exec wrapper still failed in the UAT
gate. Both the read-only gate and the staging deploy script now execute the
same bounded, read-only SQL probe against the already-verified
`tgstage_postgres` container, using loopback TCP and the container's existing
password. They no longer use `docker compose exec postgres` for schema
queries. The gate/deploy diagnostic reports the actual Alembic revision
(or EMPTY/UNEXPECTED) without exposing credentials. No migration is run by
either script.
