FROM ghcr.io/astral-sh/uv:0.12.3 AS uv
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/

# OCR runs entirely on the local worker. Include all default language packs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        poppler-utils tesseract-ocr tesseract-ocr-aze tesseract-ocr-eng tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY README.md ./README.md
# Alembic migrations are executed inside the app image during fresh installations.
# Keep the configuration and revision graph together at their expected paths.
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/var/storage /data/storage /models/cache \
    && chown -R appuser:appuser /app /data /models

USER appuser
CMD ["uv", "run", "--no-dev", "python", "-m", "office_assistant.main"]

