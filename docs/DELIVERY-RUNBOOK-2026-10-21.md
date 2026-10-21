# Fast delivery runbook — Telegram Ofis Asistenti (2026-10-21)

**Goal:** hand over a verified, reversible Telegram assistant. This document is
a deployment/acceptance procedure, not a statement that staging or company UAT
has run. Only synthetic GitHub CI currently establishes software checks.

## What is implemented

- Telegram native chat, authenticated company knowledge, source references,
  products/prices/sales facts, documents, OCR replay and Qdrant projection.
- Responsive signed Telegram Mini App and administrator-only reviewed stock
  transfer, adjustment/reservation and bounded XLSX operations. Every actual
  mutation remains behind its separate default-OFF feature flag.
- Opt-in local Restic snapshot, isolated artifact restore inspector, diagnostic
  Docling comparison, read-only operations doctor and localhost Prometheus.
- Photo candidates require confirmed references. Different-angle semantic
  matching is candidate-only and opt-in; unknown or similar photos do not
  authorize product identity, sale price, or stock.

## 1. Code quality gate (no real credentials)

At an approved commit, require both GitHub Actions quality jobs to pass.
Record exact commit SHA and action URL. On the operator staging checkout:
`uv sync --locked --dev`, `uv run --no-sync pytest -m "not integration"`,
`uv run --no-sync ruff check src tests`,
`uv run --no-sync ruff format --check src tests`, and
`uv run --no-sync pyright`. PostgreSQL/Qdrant integration tests must run
against a disposable database, **never company production**.

## 2. Quiescence, backup and disposable staging

1. Record current production commit, deployment config checksum and
   rollback owner; exclude credentials and user data from any report.
2. Obtain a write quiescence window and ensure the PostgreSQL dump plus
   original file storage form one consistent Restic snapshot. Use
   `docs/RESTIC-RECOVERY.md`, not direct backup shortcuts.
3. Perform the existing Restic artifact drill in a separate 0700 staging
   directory, using a full snapshot ID. An archive inventory check does
   **not** prove successful database restoration.
4. Restore the dump into a new, disposable PostgreSQL database and the
   original blobs into a separate private volume. Rebuild the Qdrant
   index, compare real searchable citations, source downloads and ledger
   balances without reusing production indexes.
5. If anything fails, do not enable stock writes or roll out the Mini App.

## 3. Start an isolated Mini App behind local HTTPS reverse proxy

The regular `docker compose up -d` does **not** start the Mini App:
the service belongs to the explicit `miniapp` profile. The Python HTTP
server remains localhost-only in the default non-container entry point.
The opt-in Compose service uses `MINIAPP_CONTAINER_BIND=true` on Docker's
private bridge, but **publishes only 127.0.0.1**, never a public interface.

After the operator provisions the real Telegram token, a verified
HTTPS domain, an HTTPS reverse proxy on the same host, and a **test**
environment, set these values in the uncommitted, private `.env`:

```text
MINIAPP_ENABLED=true
MINIAPP_PUBLIC_ORIGIN=https://assistant.example.org
MINIAPP_HOST_PORT=18791
MINIAPP_STOCK_TRANSFER_ENABLED=false
MINIAPP_STOCK_ACTIONS_ENABLED=false
MINIAPP_STOCK_XLSX_ENABLED=false
VISUAL_SEMANTIC_ENABLED=false
```

Replace the example origin with the actual TLS domain. Never put tokens
or private images into GitHub or a terminal screenshot. Validate the
resolved Compose configuration before starting anything:

```sh
docker compose --profile miniapp config --quiet
docker compose --profile miniapp up -d --build miniapp
docker compose --profile miniapp ps
```

`MINIAPP_CONTAINER_BIND` is supplied internally by the Compose service;
do **not** manually enable it in standalone Python or use public Docker
port mappings. Reverse proxy TLS traffic to
`http://127.0.0.1:18791` on that server only; configure the exact
Telegram Mini App URL/origin, no wildcard credentials/origins. Use an
authenticated staging bot rather than the live company bot initially.
Ensure both HTTPS and Telegram's signed initData validation work from
real clients. A live HTTP process alone is not acceptance.

## 4. Mandatory read-only smoke and operator checkpoints

From the **staging host**, after starting the Mini App profile,
run the bounded **local-only** smoke probe. It never includes a token,
company data or a stock mutation, and it must produce `SMOKE_PASS`:

```sh
uv run --no-sync python -m office_assistant.scripts.smoke_miniapp --port 18791
```

It checks HTML/JS/CSS, CSP/no-store/no-sniff headers and the rejection of
unsigned profile/stock-write calls. A passing result is not Telegram
authentication or real-device acceptance. Capture aggregate pass/fail
checks only and investigate `SMOKE_BLOCKED` before rollout.


- Execute `docker compose exec app uv run --no-dev python -m
  office_assistant.scripts.check_readiness` against the **staging**
  containers; capture the JSON readiness result privately. If it is
  `NOT_READY` or `DEGRADED`, document each reason; do not override it.
- Verify Bot /start, /menu and normal questions, permissions, company-wide
  knowledge retrieval, original source delivery, stock unknown-vs-zero,
  web-search isolation, PDF/DOCX/XLSX ingestion and correct OCR/Qdrant
  final status, not simply queued or HTTP 200.
- Verify the Mini App with active and revoked Telegram accounts, signed
  initData, Android/iOS, dark mode, slow network, timeout, restart and
  session-isolated source downloads.
- Evaluate a private, owner-labeled real photo corpus: different angles,
  clutter, look-alike products, no-match and revoked references; report
  false identity rate, candidate recall and abstentions. Never turn on
  semantic confirmation based on synthetic model tests.
- Reconcile real warehouse locations/units before any admin mutation.
  Only after separate company approval, explicitly enable one stock
  flag on **staging**, verify preview/cancel, replay refusal and ledger
  rollback. Test XLSX all-or-nothing writes and two concurrent admins.
  Production flags remain OFF until written acceptance.

## 5. Decision, rollback and delivery

Release is **BLOCKED** until the checks in §4 have signed evidence.
A green CI run does not replace the real restore drill, private photo
benchmark, live Telegram verification, or user approval.

On any incident: freeze writes, disable the three Mini App stock-write
flags and/or set `MINIAPP_ENABLED=false`, stop only the opt-in
`miniapp` profile service, restore the previously approved application
commit/config, and rerun the read-only doctor. **Never automatically
restore a database or delete volumes**; perform data recovery only with
the owner, quiescence, an independently verified snapshot and a tested
restore plan. Production ledger rollback cannot be assumed to be
reversible by redeploying code.

Operator handoff must include, privately and without secrets: tested
commit SHA, exact versions, CI job URLs, screenshot/notes of Telegram
Android/iOS UAT, readiness JSON, source-integrity and restore results,
image benchmark aggregate metrics, named sign-off, feature flag state,
recovery contact, known limitations and rollback outcome.

### Deferred vs rejected claims

Unverified: live WSL/HTTPS, live company Qdrant recovery, complete
warehouse coverage, production backup restoration, production-quality
multi-angle photo recognition and real-device UAT. Do not advertise any
of these as completed until the corresponding evidence exists.
