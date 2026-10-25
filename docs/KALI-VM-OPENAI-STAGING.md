# Fresh Kali Linux VM — isolated Telegram Office Assistant with paid OpenAI API

Status: **operator-run test environment**, not proof that the VM was installed, that a real paid API key was accepted, or that Telegram UAT passed. This procedure uses the **same application repository** on a fresh Kali VM but deliberately does **not** transfer old `qoder_*` or `tgstage_*` Docker volumes from WSL/Windows.

## A. VM requirements and safety

- Install a genuine Kali Linux VM (VirtualBox, VMware, etc.), **not a Kali Docker container**.
- Recommended staging allocation: **4 vCPU, 8–12 GiB RAM and at least 40 GiB free disk**. This is an operating guideline, not a measured minimum: Docker image builds, local multilingual embedding models and OCR need memory/storage. Use NAT for outbound connectivity; Telegram *polling* needs no inbound port forwarding.
- Log in as an ordinary sudo-enabled Linux user; keep the repository in `~/telegram-ai-staging` on the guest's own filesystem. Do **not** clone into a host-shared folder. Ensure the VM's clock and DNS are correct. Take a clean **hypervisor snapshot** before first deployment.
- Use an **independent test Telegram bot token**; if the same token is polling from the old WSL installation, the bots can steal each other's Telegram updates. Never share a bot token, API key, `.env`, logs containing secrets or private company documents in chat or GitHub.
- OpenAI API billing is distinct from a consumer ChatGPT subscription. Configure an API **project-scoped key** and project spend limits on [OpenAI Platform](https://platform.openai.com/api-keys) and [Billing](https://platform.openai.com/settings/organization/billing/overview). Requests in the synthetic probe consume billable API usage. Real private data must follow the company's chosen data policy.

## B. Install Docker and Compose V2 *inside* Kali

Kali's official Docker instructions use the package **`docker.io`**; installing the unrelated package named `docker` is wrong. Kali currently publishes a Compose V2 `docker-compose` package (the terminal interface should be **`docker compose`** with a space). Install from Kali's own repository:

```bash
sudo apt update
sudo apt install -y git curl ca-certificates python3 docker.io docker-compose
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
sudo docker run --rm hello-world
```

If the `sudo docker compose version` command does **not** show V2, do not switch to the old Python Compose V1; verify Kali's package availability with `apt-cache policy docker-compose` or use the officially documented Docker Compose plugin / Debian derivative instructions. Do not add random online install scripts.

The Docker group grants root-equivalent access; add a dedicated local user **only if this privilege is acceptable**. To avoid repeating `sudo` for every staging command:

```bash
sudo usermod -aG docker "$USER"
```

**Log out of the Kali desktop session and log back in** (or reboot) so the new group applies. Then verify as your *non-root* user:

```bash
id -nG
docker info --format '{{.OSType}}'
docker compose version
```

Expect `linux` and Docker Compose V2. Never alternate sudo and user contexts without inspecting their Docker daemon and permissions.

## C. Clone safely and run read-only collision checks

```bash
cd ~
git clone https://github.com/f1r10/telegram-ai.git telegram-ai-staging
cd ~/telegram-ai-staging
bash scripts/preflight_isolated_staging.sh
```

Expect `RESULT STAGING_PRECHECK_OK` with a warning about absent `.env`. The separate manifest `docker-compose.staging.yml` owns **`tgstage_*`** containers, network and volumes; it binds local-only host ports 55435 (Postgres), 16335 (Qdrant), 18095 (SearXNG), 18793 (optional Mini App). It does not share Docker data with old WSL. If the script says `STAGING_BLOCKED`, **do not delete or replace any container or volume**: inspect first.

**CRITICAL:** Every Docker command in this document uses `-f docker-compose.staging.yml`. Never run bare `docker compose up` from this repository, because the legacy default manifest creates `qoder_*` resources.

## D. Initialize secret-safe environment and configure paid OpenAI

Only after read-only preflight is clean, execute:

```bash
python3 scripts/init_kali_vm_env.py
nano .env
```

The initializer refuses to overwrite an existing `.env`; it generates 64-hex-character secrets for new Postgres/SearXNG, writes mode `0600`, disables all bot and stock modifications, and leaves OpenAI and Telegram credentials empty. **It prints no secret values.**

Set your private OpenAI key in the editor and confirm these exact, **single** entries are present:

```dotenv
AI_PROVIDER=openai
OPENAI_API_KEY=<YOUR_PRIVATE_OPENAI_API_KEY>
OPENAI_DEFAULT_MODEL=gpt-6-luna
OPENAI_STRONG_MODEL=gpt-6.1-sol
STRUCTURED_EXTRACTION_MODEL=gpt-6-luna
VISION_MODEL=gpt-4o-mini

TELEGRAM_BOT_TOKEN=
TELEGRAM_ACCESS_PASSWORD=
TELEGRAM_POLLING_ENABLED=false
STRUCTURED_EXTRACTION_ENABLED=false
VISUAL_SEMANTIC_ENABLED=false
MINIAPP_ENABLED=false
MINIAPP_STOCK_TRANSFER_ENABLED=false
MINIAPP_STOCK_ACTIONS_ENABLED=false
MINIAPP_STOCK_XLSX_ENABLED=false
```

No placeholder literal such as `<YOUR_PRIVATE_OPENAI_API_KEY>` may remain in an actual key field. For API access or 404 errors, verify that **this API project** can call the selected model. `gpt-6-luna` is a low-cost initial model, while `gpt-6.1-sol` is available as a stronger option where account access and budget permit; actual live permission is established by the diagnostic, not an assumption based on billing.

Check the configuration **without printing expanded secrets**:

```bash
chmod 600 .env
docker compose -f docker-compose.staging.yml --env-file .env config --quiet
bash scripts/preflight_isolated_staging.sh
```

Proceed only on `RESULT STAGING_PRECHECK_OK` with no BLOCK lines. Existing-`.env` warnings are intentional; verify it is the new Kali file.

## E. Start only local supporting services and prepare the database

```bash
docker compose -f docker-compose.staging.yml --env-file .env up -d postgres qdrant searxng
docker compose -f docker-compose.staging.yml --env-file .env ps
docker compose -f docker-compose.staging.yml --env-file .env build app worker
docker compose -f docker-compose.staging.yml --env-file .env run --rm app uv run --no-dev alembic upgrade head
docker compose -f docker-compose.staging.yml --env-file .env run --rm -e DATABASE_CHECK_ON_STARTUP=true app
```

Expected: the three dependencies eventually show `healthy`, Alembic finishes successfully and app prints a passing database health check. Do not run migrations against the old WSL project or copy its existing `.env`/database password.

Then start the worker (AI structured extraction remains OFF):

```bash
docker compose -f docker-compose.staging.yml --env-file .env up -d worker
docker compose -f docker-compose.staging.yml --env-file .env ps
docker compose -f docker-compose.staging.yml --env-file .env run --rm app uv run --no-dev python -m office_assistant.scripts.check_readiness
```

Expect a healthy, stable `tgstage_worker` and an overall `READY` value, with PDF/OCR, PostgreSQL, job queues, Qdrant and live SearXNG checks marked `OK`. An empty clean DB cannot demonstrate a real successful file import or company-facing answer.

## F. Probe the paid OpenAI API without sending company data

The diagnostic sends only **two synthetic source-answer requests** to the selected account. It incurs API usage. Run:

```bash
docker compose -f docker-compose.staging.yml --env-file .env run --rm app uv run --no-dev python -m office_assistant.scripts.check_ai
```

Expect two `AI_CHECK=OK provider=openai` lines for `GeneralModelOutput` and `GroundedModelOutput`. If it reports 401, 403, 404, 429 or `insufficient_quota`, stop the bot test and verify the **API account's** project key, model availability, spend limit and balance. Do not post the key or unredacted client logs. A successful API probe does not mean every company function has passed live acceptance.

## G. Activate a *separate* Telegram test bot and check end-to-end

Use Telegram [@BotFather](https://t.me/BotFather) to create a **new** test bot; do not reuse a token active on another machine. Edit `.env` privately:

```dotenv
TELEGRAM_BOT_TOKEN=<PRIVATE_NEW_TEST_BOT_TOKEN>
TELEGRAM_ACCESS_PASSWORD=<PRIVATE_UNIQUE_TEST_PASSWORD>
TELEGRAM_POLLING_ENABLED=true
```

Keep all dangerous stock/Mini App/structured extraction switches OFF. Recreate **only** the staging bot application:

```bash
docker compose -f docker-compose.staging.yml --env-file .env config --quiet
docker compose -f docker-compose.staging.yml --env-file .env up -d --no-deps app
docker compose -f docker-compose.staging.yml --env-file .env ps
docker inspect tgstage_app --format 'Status={{.State.Status}} RestartCount={{.RestartCount}}'
```

Telegram acceptance checklist, in order:

1. `/start` — prompts for private access password; first successfully authenticated user is staged admin.
2. `/menu` — shows simple office operations, not a debug dump.
3. `Salam, sən hansı işləri görə bilirsən?` — Azerbaijani normal chat.
4. `/baza QA-EMPTY-001 məhsulunun zəmanəti nə qədərdir?` — must not invent company facts from the empty DB.
5. `Bazaya yaz: QA-TXT-221 kodlu sınaq məhsulunun zəmanəti 19 aydır.`, approve `Yadda saxla`, wait for indexing, ask `/baza QA-TXT-221 məhsulunun zəmanət müddəti neçə aydır?`: answer 19 months with source.
6. Use only synthetic PDF/DOCX/XLSX files; confirm the answer is **concise**, cites the correct file, distinguishes cost from selling price, does not mistake a mere quote for a real sale and does not expose raw spreadsheet contents.
7. Rerun `check_readiness`; check app and worker restart counts. No production data is needed to complete these tests.

If a query reports a temporary technical failure, do not keep hammering the paid API. Capture **redacted** error category and type using local logs:

```bash
docker compose -f docker-compose.staging.yml --env-file .env logs --tail=70 app
```

**Inspect and redact** before sharing: logs may contain customer data, chat text, bot credentials or file names. Never paste `.env` or full Docker Compose `config` output.

## H. What this procedure does NOT validate

- It does not transfer WSL/Windows data to Kali. The two installations remain fully independent.
- It does not enable real stock writes, production sales, automatic price approvals, Mini App public HTTPS or confidential document processing. Those need separate access-control/data-protection review and supervised acceptance.
- A healthy startup is not equivalent to real scanned PDF/image/Excel accuracy, fault recovery, backup-and-restore testing or a production deployment.
- To *suspend the test bot without deleting any data*: set `TELEGRAM_POLLING_ENABLED=false` in `.env`, then recreate only `app` with `up -d --no-deps --force-recreate app`. Do not use `docker compose down -v`, `docker volume prune` or `docker system prune`.


## I. Standalone PNG/JPEG label OCR and previous deferred images

The staged worker supports **local Tesseract OCR** for standalone PNG/JPEG documents when `IMAGE_OCR_ENABLED=true` (Compose default). Images remain stored as image assets, but readable printed text becomes a source-citable `image_ocr` block and is indexed in the existing Qdrant pipeline. No uploaded image is transmitted to OpenAI simply because it was saved. OCR extraction is **not** approval of product identity, price, stock or commercial records. If text cannot be read reliably the image stays `DEFERRED`, not a false indexing success.

On older staging installations, an image sent **as a Telegram document** before this feature remains historically deferred. After updating code and **rebuilding both app and worker**, an operator may explicitly queue a safe replay of the original bytes:

```bash
# Non-mutating preview, shows only counts; no image content or API keys
docker compose -f docker-compose.staging.yml --env-file .env run --rm app uv run --no-dev python -m office_assistant.scripts.replay_deferred_images --limit 25
# Only if eligible > 0 and a snapshot/backup exists:
docker compose -f docker-compose.staging.yml --env-file .env run --rm app uv run --no-dev python -m office_assistant.scripts.replay_deferred_images --apply --limit 25
# Verify worker/index queue and latest-state readiness afterward
docker compose -f docker-compose.staging.yml --env-file .env run --rm app uv run --no-dev python -m office_assistant.scripts.check_readiness
```

Existing assets, original bytes, session associations and original parse-run records are preserved. Replay creates new parse provenance and reuses the durable job/index mechanism; it never silently approves catalog or inventory changes. Historical `DEFERRED` records remain auditable but do not keep readiness degraded after a successful new parse. Never manually delete Docker volumes to clear a warning.

**Distinct tests:** (1) Send a printed-label PNG as a **document** and save it; wait until indexed, then ask `/baza QA-IMG-682 məhsulunun qutu miqdarı və partiya kodu nədir?` and check the source filename. (2) For on-demand **OpenAI Vision**, send the picture as a Telegram **photo with the `/baza ...` question in the same message's caption**; a separate later `/baza Şəkildəki ...` message has no attached image and cannot implicitly refer to the earlier file. These are different features. If local OCR cannot read stylized handwriting or nontext visuals, do not infer stock or product identity. Use only synthetic samples until private-data handling is approved.

## Staging visual catalog one-command activation (PR #103)

From a clean staging main checkout, the operator only needs to run:

    git pull --ff-only origin main
    bash scripts/activate_catalog_visual_staging.sh

The script safely backs up .env to .env.before-catalog-visual.bak,
sets only VISUAL_SEMANTIC_ENABLED=true and
VISUAL_SEMANTIC_MAX_REFERENCES=200, validates Compose, builds the app,
explicitly downloads and warms the local CLIP model, then recreates only
the staging app and runs the local model-and-catalog readiness probe.

Do NOT claim success without CATALOG_VISUAL_ACTIVATION=READY and a
CATALOG_VISUAL_LOCAL status of READY. The model download requires
network connectivity only for this operator-controlled installation.
The script never removes Docker volumes or restarts the database/worker.
Verify actual different-photo recall on Telegram separately.


## Visual catalog deployment verification (read-only)

After building the staging app from the merged source, run:

    docker compose -f docker-compose.staging.yml --env-file .env run --rm app \
      uv run --no-dev python -m office_assistant.scripts.diagnose_catalog_visual --probe-model

Output must show status READY, semantic_enabled true, and active_pdf_images > 0.
SEMANTIC_DISABLED indicates the effective .env flag overrides Compose defaults;
MODEL_OR_EMBEDDINGS_UNAVAILABLE indicates the model could not embed a sample.
Do not claim internet-photo support when this probe fails. The probe does not
download a model or send any photo to a remote provider.

## Visual catalog matching after PR #101: model provisioning and acceptance

The original PDF image index is internal draw-order metadata and may not equal
the first/second picture visible on the page. Telegram now displays top-down
visual position only when reliable PDF geometry is available.

Staging supports catalog photo similarity using the local CLIP image encoder,
in addition to byte and perceptual-hash near duplicates. This is **not**
automatic creation/approval of products or stock entries.

After pulling main and rebuilding app, run once to provision and warm the model
on the staging model-cache volume (requires network access for initial model
weights; ordinary Telegram turns remain offline-only for visual inference):

```bash
docker compose -f docker-compose.staging.yml --env-file .env run --rm app \
  uv run --no-dev python -m office_assistant.scripts.prepare_catalog_visual_references \
  --download-model --apply --limit 200
```

A ready output includes CATALOG_MODEL_STATUS=PROVISIONED and
CATALOG_VISUAL_PREWARM_STATUS=APPLIED with warmed greater than zero.
The dry-run command without flags only reports eligible image occurrences.
If .env explicitly sets VISUAL_SEMANTIC_ENABLED=false, change it to true
before the next app rebuild. Set VISUAL_SEMANTIC_MAX_REFERENCES=200 for
this catalog acceptance corpus if that key exists with the old 80 limit.
Never print or share secrets from .env.

Test the 100 cm cone and wet-floor-sign internet photo. A different-angle
match must be labeled a *semantic candidate*, never a confirmed SKU. Verify the
original PDF is available through Mənbə 1. Check that page 1 top cone
and page 3 top chain roll are shown as visual position 1 even where their
internal XObject indices differ. Verify an unrelated photo is not promoted to
a confirmed product. Capture false matches and missing matches separately:
the uncalibrated similarity score is not a product-identity probability.

## J. Debugging an indexed image that the bot cannot find

A report of `READY`, `indexed_chunks=1` or `historical_pdf_ocr_replay indexed=1` confirms that the pipeline completed, **not** that Tesseract correctly read a label. Before adjusting the model or relevance threshold, run the built-in **read-only, content-redacted** test against one synthetic image:

```bash
docker compose -f docker-compose.staging.yml --env-file .env run --rm app \
  uv run --no-dev python -m office_assistant.scripts.diagnose_image_ocr \
  --filename QA_IMG_682_sintetik_etiket.png --code QA-IMG-682 \
  --quantity 8 --lot L-731 --probe-search
```

The output contains only statuses, counts and exact-token Boolean flags, never the image, raw OCR text, user messages, credentials or a provider API request. `ocr_code_exact=false` indicates local OCR could not read the exact target identifier; a higher-confidence search threshold cannot repair that. `ocr_code_exact=true` with `target_top10_hits=0` indicates retrieval/ranking problems. `target_filtered_hits>0` with `target_filtered_accepted=0` indicates the relevance gate rejected a result. Diagnostic commands must not alter or fabricate official product/catalog identities.

For **on-demand OpenAI Vision** the picture must be included in the *same turn* as the question: send a photo **with a caption** beginning `/baza ...`, or **reply directly to the existing photo/document message** with `/baza Şəkildəki məhsul kodu və partiya nədir?`. Sending `/baza Şəkildəki ...` as a new, unrelated text message gives the bot no image context. Vision sends only the image attached to that explicit question to the configured provider; it is not an ingestion action.

Operator caution: run Kali under a dedicated unprivileged user, not the `root` desktop account, for routine staging operations. Docker group access remains root-equivalent and requires trusted local operators.
