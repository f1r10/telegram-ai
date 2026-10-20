"""Opt-in Mini App process, isolated from Telegram polling and bound to loopback.

Launch only after the operator configures an HTTPS reverse proxy forwarding
to this local port and an approved Mini App URL on the Telegram bot.
"""

import asyncio
import logging
import threading
from pathlib import Path

from office_assistant.ai.provider import OpenAILanguageModel
from office_assistant.ai.service import GroundedAnswerService
from office_assistant.application.ingestion import IngestionSessionService
from office_assistant.application.inventory import InventoryLedgerService
from office_assistant.application.sales_ledger import SalesLedgerService
from office_assistant.business_tools.registry import BusinessToolRegistry
from office_assistant.business_tools.service import BusinessQueryService
from office_assistant.core.logging import configure_logging
from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.internal_evidence.delivery import SourceFileDeliveryService
from office_assistant.internal_evidence.document_search import (
    DocumentRelevancePolicy,
    InternalDocumentSearch,
)
from office_assistant.internal_evidence.router import EvidencePlanner
from office_assistant.internal_evidence.service import InternalEvidenceService
from office_assistant.internal_evidence.tool_adapter import OpenAIToolSchemaAdapter
from office_assistant.knowledge.embeddings import FastEmbedSparseAdapter, FastEmbedTextAdapter
from office_assistant.knowledge.qdrant_store import QdrantKnowledgeStore
from office_assistant.knowledge.retrieval import KnowledgeRetrievalService
from office_assistant.miniapp.backend import MiniAppBackend
from office_assistant.miniapp.http import MiniAppHTTPServer
from office_assistant.miniapp.photo_lookup import MiniAppPhotoLookup
from office_assistant.miniapp.progress import MiniAppDocumentProgress
from office_assistant.miniapp.uploads import MiniAppUploadWorkflow
from office_assistant.processing.semantic_visual import SemanticVisualSearch

logger = logging.getLogger(__name__)


def main() -> None:
    settings = load_settings()
    if not settings.miniapp_enabled:
        raise SystemExit("Mini App is OFF by default; set MINIAPP_ENABLED=true only on staging")
    if settings.telegram_bot_token is None or not settings.miniapp_public_origin:
        raise SystemExit("Signed Telegram bot token and HTTPS Mini App origin are required")
    configure_logging(
        settings.log_level,
        [
            secret.get_secret_value()
            for secret in (settings.telegram_bot_token, settings.openai_api_key)
            if secret is not None
        ],
    )
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="miniapp-async", daemon=True)
    thread.start()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    ai: OpenAILanguageModel | None = None
    qdrant: QdrantKnowledgeStore | None = None
    evidence: InternalEvidenceService | None = None

    try:
        if settings.openai_api_key is not None:
            ai = OpenAILanguageModel(
                settings.openai_api_key.get_secret_value(),
                default_model=settings.openai_default_model,
                strong_model=settings.openai_strong_model,
                vision_model=settings.vision_model,
                timeout_seconds=settings.openai_request_timeout_seconds,
                max_output_tokens=settings.openai_max_output_tokens,
            )
            retrieval: KnowledgeRetrievalService | None = None
            if settings.qdrant_url:
                qdrant = QdrantKnowledgeStore(
                    settings.qdrant_url,
                    alias=settings.qdrant_collection_alias,
                    collection=settings.qdrant_collection_name,
                )
                retrieval = KnowledgeRetrievalService(
                    qdrant,
                    FastEmbedTextAdapter(
                        settings.embedding_model,
                        settings.embedding_cache_path,
                        batch_size=settings.embedding_batch_size,
                    ),
                    FastEmbedSparseAdapter(cache_dir=settings.embedding_cache_path),
                )
            grounding = GroundedAnswerService(
                session_factory,
                retrieval,
                ai,
                retrieval_limit=settings.rag_retrieval_limit,
                max_context_tokens=settings.rag_max_context_tokens,
                history_max_messages=settings.chat_history_max_messages,
                history_max_tokens=settings.chat_history_max_tokens,
            )
            registry = BusinessToolRegistry(BusinessQueryService(session_factory))
            evidence = InternalEvidenceService(
                session_factory,
                registry,
                InternalDocumentSearch(
                    retrieval,
                    DocumentRelevancePolicy(
                        min_hybrid_score=settings.internal_rag_min_score,
                        min_identifier_score=settings.internal_rag_identifier_min_score,
                    ),
                ),
                EvidencePlanner(ai, OpenAIToolSchemaAdapter(registry)),
                ai,
                grounding,
                document_limit=settings.internal_rag_limit,
                source_ttl_minutes=settings.recent_source_context_ttl_minutes,
                source_context_messages=settings.recent_source_context_messages,
                max_tool_calls=settings.internal_tool_max_calls,
            )

        storage = LocalFileStorage(settings.file_storage_path)
        visual = (
            SemanticVisualSearch(
                storage_root=storage.root,
                model_cache_root=Path(settings.embedding_cache_path),
                version=settings.visual_semantic_model_version,
                min_score=settings.visual_semantic_min_score,
                max_references=settings.visual_semantic_max_references,
            )
            if settings.visual_semantic_enabled
            else None
        )
        backend = MiniAppBackend(
            session_factory,
            bot_token=settings.telegram_bot_token.get_secret_value(),
            evidence=evidence,
            inventory=InventoryLedgerService(session_factory),
            stock_transfer_enabled=settings.miniapp_stock_transfer_enabled,
            products=BusinessQueryService(session_factory),
            sales=SalesLedgerService(session_factory),
            file_delivery=SourceFileDeliveryService(
                session_factory,
                storage,
                max_bytes=min(settings.file_delivery_max_bytes, settings.max_upload_bytes),
            ),
            progress=MiniAppDocumentProgress(session_factory),
            photo_lookup=MiniAppPhotoLookup(
                session_factory,
                storage_root=storage.root,
                semantic=visual,
            ),
            uploads=MiniAppUploadWorkflow(
                storage,
                IngestionSessionService(
                    session_factory,
                    storage,
                    max_files=settings.max_files_per_pending_session,
                    ttl_hours=settings.pending_session_ttl_hours,
                    max_upload_bytes=settings.max_upload_bytes,
                ),
            ),
            rate_limit_per_minute=settings.ai_rate_limit_per_minute,
            request_timeout_seconds=min(75.0, settings.miniapp_request_timeout_seconds),
        )
        with MiniAppHTTPServer(
            port=settings.miniapp_local_port,
            public_origin=settings.miniapp_public_origin,
            backend=backend,
            loop=loop,
        ) as server:
            logger.info("Mini App listening only on 127.0.0.1:%s", settings.miniapp_local_port)
            server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        logger.info("Mini App shutdown requested")
    finally:

        async def close_resources() -> None:
            if ai is not None:
                await ai.close()
            if qdrant is not None:
                await qdrant.close()
            await engine.dispose()

        task = asyncio.run_coroutine_threadsafe(close_resources(), loop)
        try:
            task.result(timeout=20)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            loop.close()


if __name__ == "__main__":
    main()
