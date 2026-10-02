# Office Assistant

Private internal company AI assistant foundation. The planned primary interface is a Telegram bot in Azerbaijani, backed by company data, documents, and controlled external search.

## Current status

This repository currently contains **Phase 1**:

- typed environment settings;
- lightweight redacted logging;
- minimal application entrypoint;
- deterministic `uv` dependency management;
- pytest, Ruff, and Pyright configuration;
- Docker image and minimal Compose foundation;
- PostgreSQL 17 persistence foundation with async SQLAlchemy;
- Alembic migration configuration and the initial technical migration;
- PostgreSQL health check and integration tests;
- architecture index and the approved web-search ADR.

The following are **planned, not implemented**: Qdrant, OpenAI calls, RAG, document parsing, web search, business entities, image search, reports, and worker jobs. Phase 3 implements raw `/saxla` staging and job creation, but does not parse or index files.

## Prerequisites

- Python 3.12
- `uv`
- Docker Desktop (for the container smoke test)

Python 3.12 is selected for the project and is enforced by `pyproject.toml`.

## Local setup

```powershell
uv sync --dev
Copy-Item .env.example .env
uv run python -m office_assistant.main
```

Set `POSTGRES_PASSWORD` in `.env` before starting PostgreSQL. When running Python directly on Windows, use `POSTGRES_HOST=localhost`. Inside Compose, the application uses `POSTGRES_HOST=postgres`.

## PostgreSQL and migrations

```powershell
docker compose up -d postgres
uv run alembic upgrade head
uv run python -m office_assistant.main
```

Schema changes are migration-driven. Application startup does not run `create_all` or silently apply migrations.

To verify the database explicitly during a smoke check, set `DATABASE_CHECK_ON_STARTUP=true` and run the application after PostgreSQL is healthy.

## Telegram Phase 2

Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ACCESS_PASSWORD`, and `TELEGRAM_POLLING_ENABLED=true` in `.env`. Run migrations first, then start the application:

```powershell
uv run alembic upgrade head
uv run python -m office_assistant.main
```

Local mode uses long polling and private chats only. The first user who submits the shared access password becomes the active `ADMIN`. Later valid requests become `PENDING` until the administrator approves them from `/users` using inline buttons. Active users are recognized by their numeric Telegram user ID and are not asked for the shared password again.

Implemented commands are `/start`, `/whoami`, `/users`, `/saxla`, and `/cancel`. `/users` is server-side ADMIN-only. User roles and statuses are persisted in PostgreSQL and security-sensitive mutations are written to `audit_events`.

The shared access password is a one-time entry secret, not a permanent user password. It is never stored in PostgreSQL or logs. Failed attempts have a bounded in-process cooldown suitable for the small V1 deployment. Telegram usernames are display metadata only; numeric Telegram IDs are authoritative.

## Raw file ingestion

Active ADMIN and USER accounts may upload PDF, DOCX, XLSX, TXT, JPG, JPEG, and PNG files in a private chat. Files are staged under `FILE_STORAGE_PATH/staged` and are not treated as company knowledge. Captions and ordinary text sent while a session is open are preserved in order as session items.

`/saxla` is the commit boundary. It moves staged bytes into `FILE_STORAGE_PATH/blobs`, marks the session committed, creates exactly one `PENDING` `ingestion_jobs` row, and returns a compact Azerbaijani summary. Exact duplicate bytes are identified by incremental SHA-256; duplicate submissions retain their own session item/provenance while reusing the physical blob.

`/cancel` cancels the user's open session and removes unreferenced staged files. Open sessions expire lazily after `PENDING_SESSION_TTL_HOURS`; deterministic cleanup is available with:

```powershell
uv run python -m office_assistant.scripts.cleanup_staging
```

`MAX_UPLOAD_BYTES` and `MAX_FILES_PER_PENDING_SESSION` are operational safety controls, not product-level file-count limits. No parser, OCR, embedding, AI call, or worker runs during upload or `/saxla`.

## Quality checks

```powershell
uv run pytest
uv run pytest -m integration
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

## Docker

```powershell
docker compose build
docker compose run --rm app
```

Phase 3 still includes only the application and PostgreSQL services. The app also mounts the persistent `app_storage` volume at `/data/storage`. Qdrant, worker, and private SearXNG will be introduced in later phases after their foundations are needed. PostgreSQL is persistent through the `postgres_data` Docker volume and is not publicly exposed by default in the production architecture.

## Dependency policy

Runtime dependencies: `pydantic-settings` provides typed environment configuration; SQLAlchemy 2.x provides the typed async ORM/Core layer; `asyncpg` provides the async PostgreSQL driver; Alembic provides migration management. Each can be replaced behind the persistence boundary later, though the current stack is the approved foundation.

Development dependencies: `pytest` for tests, `ruff` for linting/formatting, and `pyright` for static type checking. Logging uses the Python standard library, so no observability stack is added.

`uv` is used for project dependencies, the lock file, local commands, and Docker installation. Do not introduce Poetry, pipenv, Conda, or another dependency manager.

## Secrets

Never commit Telegram tokens, OpenAI keys, database passwords, Tavily/Exa keys, backup credentials, or production `.env` files. `.env.example` contains placeholders only.

