"""Deterministically expire open sessions and clean staged files."""

import asyncio

from office_assistant.application.ingestion import IngestionSessionService
from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage


async def run() -> int:
    settings = load_settings()
    engine = create_database_engine(settings)
    storage = LocalFileStorage(settings.file_storage_path)
    service = IngestionSessionService(
        create_session_factory(engine),
        storage,
        max_files=settings.max_files_per_pending_session,
        ttl_hours=settings.pending_session_ttl_hours,
        max_upload_bytes=settings.max_upload_bytes,
    )
    try:
        return await service.cleanup_expired()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    print(f"Expired sessions cleaned: {asyncio.run(run())}")
