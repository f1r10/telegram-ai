# Windows / WSL2 / Docker — isolated Telegram assistant staging

Status: **operator procedure; not proof of installation or user acceptance**. Use the reviewed `main` commit. The source is the `f1r10/telegram-ai` repository; never paste production tokens, the private `.env`, raw test photos or company documents into chat or GitHub.

## A. Windows PowerShell — discover, do not install yet

Run in **PowerShell**, not inside WSL:

```powershell
wsl --status
wsl -l -v
docker version
```

Expected: an Ubuntu-like distribution at **VERSION 2** and a Docker **Server** in Linux-container mode. If unavailable, install/repair WSL2 and Docker Desktop from Microsoft/Docker official installers, start Docker Desktop, and enable `Settings → Resources → WSL Integration` for the selected distro. Do not enable network-facing daemon TCP ports. Docker Desktop should use the WSL2 backend. Do not mix `sudo docker` and unprivileged Docker Desktop context without checking what daemon each reaches.

## B. WSL Ubuntu — clone into the Linux filesystem

On a fresh WSL distro, open Ubuntu. Ensure `git` is installed via the distro package manager if absent. Clone into the Linux home directory, **not** `/mnt/c` or Desktop/Downloads:

```bash
cd ~
git clone https://github.com/f1r10/telegram-ai.git telegram-ai-staging
cd ~/telegram-ai-staging
bash scripts/preflight_wsl2.sh
```

If `telegram-ai-staging` already exists, do **not** overwrite it with `git clone`; inspect `git status`, preserve edits, and update with `git pull --ff-only origin main` only after confirming the checkout is the intended clean staging clone.

Preflight is deliberately read-only: checks for WSL2, Docker CLI/engine, Docker Compose V2, a Linux-native directory, private `.env` existence and unsafe write flags, space, **fixed** `qoder_*` container and volume name collisions. Share only its `PASS/WARN/FAIL/RESULT` text, not credentials or private `.env`. A clean preflight does not imply the Telegram bot, AI API, OCR, Qdrant or Mini App was tested.

### Collision warning

The current `docker-compose.yml` uses fixed names (`qoder_app`, `qoder_postgres`, etc.), fixed Docker volumes (`qoder_postgres_data`, etc.), and host-only ports **5435** (PostgreSQL), **6335** (Qdrant), **18085** (SearXNG), and **18791** (opt-in Mini App). Some of these were chosen to coexist with an earlier instance. **If a preflight warning says an existing `qoder_*` container or volume was found, STOP.** Check whether it contains company data or an earlier stack. Do not run `up`, `down -v`, `prune` or remove a volume to resolve the collision. Resolve separation of stack names/volumes and backup ownership before launch.

## C. Prepare a PRIVATE staging environment — only after collision check

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Required initially: a unique, strong `POSTGRES_PASSWORD`, separate test-only `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ACCESS_PASSWORD` **when ready for bot testing**, a private `SEARXNG_SECRET`, and an OpenAI API key only when a billable AI test is approved. Do not copy a production `.env` to staging.

Keep these exact safe choices for the first infrastructure pass:

```text
TELEGRAM_POLLING_ENABLED=false
MINIAPP_ENABLED=false
MINIAPP_STOCK_TRANSFER_ENABLED=false
MINIAPP_STOCK_ACTIONS_ENABLED=false
MINIAPP_STOCK_XLSX_ENABLED=false
VISUAL_SEMANTIC_ENABLED=false
STRUCTURED_EXTRACTION_ENABLED=false
```

The last setting simply postpones billable AI extraction during infrastructure smoke. Later restore it to `true` with an authorized test API key and a representative private corpus. Verify `OPENAI_DEFAULT_MODEL` using the account's model availability and the explicit diagnostic. No real source documents are required for initial startup.

The Compose file injects `POSTGRES_HOST=postgres` and `QDRANT_URL=http://qdrant:6333` inside containers; the local `.env.example` uses `localhost` for host tools. Do not change these container endpoints blindly.

## D. Isolated infrastructure smoke

Only if the preflight has no blocking conditions or ownership questions and the local `.env` was reviewed:

```bash
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d postgres qdrant searxng
docker compose --env-file .env build app
docker compose --env-file .env run --rm app uv run --no-dev alembic upgrade head
docker compose --env-file .env up -d app worker
docker compose --env-file .env ps
docker compose --env-file .env exec -T app uv run --no-dev python -m office_assistant.scripts.check_readiness
```

**Database migrations affect the selected staging DB**; never run against a production or unidentified PostgreSQL volume. If the readiness output is `NOT_READY` or `DEGRADED`, investigate rather than claiming acceptance. Check logs privately with `docker compose logs --tail=100 app worker`; log text may contain business data. Never paste entire logs with tokens/user content into the conversation.

Stopping containers without deleting persistent volumes (only after confirming this is your staging stack):

```bash
docker compose --env-file .env stop app worker
```

**Never** use `docker compose down -v`, `docker volume prune`, or reset a company database as a troubleshooting shortcut. The old `docs/V2-MANUAL-UAT.md` mentions `docker-compose.local.yml`, which is not in this repository; this procedure uses the actual existing `docker-compose.yml`.

## E. Enable the test Telegram bot, and then the Mini App separately

After an approved infrastructure health check and test-only credentials, set `TELEGRAM_POLLING_ENABLED=true`, recreate **only the staging app** and verify a single active poller:

```bash
docker compose --env-file .env up -d --no-deps --force-recreate app
```

Use `/start` and `/menu` with **two dedicated test Telegram accounts**. Proceed with synthetic TXT/PDF/DOCX/XLSX, source downloads, rejection of unknown stock and explicit /saxla. Run the OpenAI diagnostic on the staging app before declaring chat healthy:

```bash
docker compose --env-file .env exec -T app uv run --no-dev python -m office_assistant.scripts.check_openai
```

This makes small billable API requests if an OpenAI key is configured. Do not trigger it without approval.

The Telegram **Mini App requires a real HTTPS public origin**, a test bot, an operator-owned TLS reverse proxy to `127.0.0.1:18791` and valid Telegram-signed `initData`; plain `localhost` is not a real-phone HTTPS acceptance URL. It remains OFF during basic bot smoke. Once HTTPS is verified and approved, refer to `docs/DELIVERY-RUNBOOK-2026-10-21.md` to enable the explicit `miniapp` Compose profile and run the local-only `smoke_miniapp` script. All stock-write flags remain OFF until explicit separate signoff.

## F. Evidence required for customer handoff

- Exact staging commit + green `main` quality run.
- Non-secret preflight output, Compose status, read-only readiness JSON and authenticated test-bot UAT notes.
- Verified original file retrieval, correct supplier price/column roles, no cross-user data leakage, and correct idempotence for exact duplicate bytes.
- Private representative product photos and target SKU labels to measure multi-angle candidate recall and false matches.
- Safe original-file/PostgreSQL snapshot and **isolated restore** proof, not just a backup-success message.
- HTTPS Mini App on real Android/iOS clients, test account revocation, and explicit approval of every optional stock-write capability.

Do not mark production release complete solely because CI or Docker Compose reports success.
