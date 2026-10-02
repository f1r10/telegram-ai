# Office Assistant

Private internal company AI assistant foundation. The planned primary interface is a Telegram bot in Azerbaijani, backed by company data, documents, and controlled external search.

## Current status

This repository currently contains **Phase 0 only**:

- typed environment settings;
- lightweight redacted logging;
- minimal application entrypoint;
- deterministic `uv` dependency management;
- pytest, Ruff, and Pyright configuration;
- Docker image and minimal Compose foundation;
- architecture index and the approved web-search ADR.

The following are **planned, not implemented**: Telegram flows, authentication, PostgreSQL, Qdrant, OpenAI calls, RAG, document parsing, `/saxla`, web search, business entities, image search, reports, and worker jobs.

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

The Phase 0 process only loads settings, initializes logging, and exits cleanly.

## Quality checks

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

## Docker

```powershell
docker compose build
docker compose run --rm app
```

Phase 0 intentionally includes only the application service. PostgreSQL, Qdrant, worker, and private SearXNG will be introduced in their respective phases after their foundations are needed.

## Dependency policy

Runtime dependency: `pydantic-settings` provides typed environment configuration and can be replaced by another typed settings implementation later.

Development dependencies: `pytest` for tests, `ruff` for linting/formatting, and `pyright` for static type checking. Logging uses the Python standard library, so no observability stack is added.

`uv` is used for project dependencies, the lock file, local commands, and Docker installation. Do not introduce Poetry, pipenv, Conda, or another dependency manager.

## Secrets

Never commit Telegram tokens, OpenAI keys, database passwords, Tavily/Exa keys, backup credentials, or production `.env` files. `.env.example` contains placeholders only.

