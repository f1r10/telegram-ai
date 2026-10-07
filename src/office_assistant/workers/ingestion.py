"""Simple PostgreSQL polling worker for deterministic document processing."""

import asyncio
import logging
import signal

from office_assistant.ai.provider import OpenAILanguageModel
from office_assistant.core.clock import SystemClock
from office_assistant.core.logging import configure_logging
from office_assistant.core.settings import load_settings
from office_assistant.extraction.service import StructuredExtractionService
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.health import check_clock_drift
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.knowledge.embeddings import FastEmbedSparseAdapter, FastEmbedTextAdapter
from office_assistant.knowledge.indexing import KnowledgeIndexService
from office_assistant.knowledge.models import ChunkingConfig
from office_assistant.knowledge.qdrant_store import QdrantKnowledgeStore
from office_assistant.processing.worker_service import IngestionJobService, worker_id
from office_assistant.reminders.dispatch import ReminderDispatchService


async def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    engine = create_database_engine(settings)
    # The writer of leases and reminder deadlines must agree with the server that stamps them.
    await check_clock_drift(engine, threshold_seconds=settings.clock_drift_warning_seconds)
    factory = create_session_factory(engine)
    service = IngestionJobService(
        factory,
        LocalFileStorage(settings.file_storage_path),
        lease_seconds=settings.worker_lease_seconds,
        max_attempts=settings.worker_max_attempts,
        index_collection_alias=settings.qdrant_collection_alias,
        index_collection_name=settings.qdrant_collection_name,
        index_embedding_model=settings.embedding_model,
        index_chunking_version=settings.chunking_version,
        structured_extraction_enabled=settings.structured_extraction_enabled,
    )
    indexer: KnowledgeIndexService | None = None
    qdrant_store = None
    extraction_model = None
    extractor = None
    reminders = ReminderDispatchService(
        factory,
        clock=SystemClock(),
        lease_seconds=settings.worker_lease_seconds,
    )
    if settings.structured_extraction_enabled and settings.openai_api_key is not None:
        extraction_model = OpenAILanguageModel(
            settings.openai_api_key.get_secret_value(),
            default_model=settings.structured_extraction_model,
            strong_model=settings.openai_strong_model,
            timeout_seconds=settings.openai_request_timeout_seconds,
            max_output_tokens=settings.structured_extraction_max_output_tokens,
        )
        extractor = StructuredExtractionService(
            factory,
            LocalFileStorage(settings.file_storage_path),
            extraction_model,
            model=settings.structured_extraction_model,
            max_chars=settings.structured_extraction_max_input_chars,
            batch_size=settings.structured_extraction_batch_size,
            review_ttl_hours=settings.structured_review_ttl_hours,
            lease_seconds=settings.worker_lease_seconds,
            max_attempts=settings.worker_max_attempts,
        )
    if settings.qdrant_url:
        qdrant_store = QdrantKnowledgeStore(
            settings.qdrant_url,
            alias=settings.qdrant_collection_alias,
            collection=settings.qdrant_collection_name,
        )
        indexer = KnowledgeIndexService(
            factory,
            LocalFileStorage(settings.file_storage_path),
            qdrant_store,
            FastEmbedTextAdapter(
                settings.embedding_model,
                settings.embedding_cache_path,
                batch_size=settings.embedding_batch_size,
            ),
            FastEmbedSparseAdapter(cache_dir=settings.embedding_cache_path),
            chunking=ChunkingConfig(
                settings.chunking_version,
                settings.chunk_max_chars,
                settings.chunk_overlap_chars,
            ),
            lease_seconds=settings.worker_lease_seconds,
            max_attempts=settings.worker_max_attempts,
        )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop.set)
        except NotImplementedError:
            pass
    identity = worker_id()
    logger = logging.getLogger(__name__)
    logger.info("Ingestion worker started | worker_id=%s", identity)
    try:
        while not stop.is_set():
            claim = await service.claim_next(identity)
            if claim is not None:
                await service.process(claim)
                continue
            index_claim = await indexer.claim_next(identity) if indexer is not None else None
            if index_claim is not None and indexer is not None:
                await indexer.process(index_claim)
                continue
            extraction_claim = (
                await extractor.claim_next(identity) if extractor is not None else None
            )
            if extraction_claim is not None and extractor is not None:
                await extractor.process(extraction_claim)
                continue
            await reminders.reclaim_stale()
            if await reminders.claim_due() > 0:
                continue
            if claim is None:
                try:
                    await asyncio.wait_for(stop.wait(), settings.worker_poll_interval_seconds)
                except TimeoutError:
                    pass
                continue
            await service.process(claim)
    finally:
        if qdrant_store is not None:
            await qdrant_store.close()
        if extraction_model is not None:
            await extraction_model.close()
        await engine.dispose()
        logger.info("Ingestion worker stopped | worker_id=%s", identity)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
