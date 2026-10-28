"""Minimal Phase 0 application entrypoint."""

import asyncio
import logging
from pathlib import Path

from office_assistant.ai.factory import create_language_model
from office_assistant.ai.service import GroundedAnswerService, InProcessRateGuard
from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.ingestion import IngestionSessionService
from office_assistant.application.inventory import InventoryLedgerService
from office_assistant.application.reference_images import ReferenceImageService
from office_assistant.application.reminders import ReminderService
from office_assistant.application.sales_ledger import SalesLedgerService
from office_assistant.application.user_management import UserManagementService
from office_assistant.business_tools.registry import BusinessToolRegistry
from office_assistant.business_tools.service import BusinessQueryService
from office_assistant.core.clock import SystemClock
from office_assistant.core.logging import configure_logging
from office_assistant.core.settings import Settings, load_settings
from office_assistant.extraction.review import ExtractionReviewService
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.health import (
    check_clock_drift,
    check_database_health,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.integrations.telegram.app import create_telegram_application
from office_assistant.integrations.telegram.handlers import TelegramServices
from office_assistant.integrations.telegram.state import PasswordAttemptLimiter, PasswordEntryState
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
from office_assistant.processing.semantic_visual import SemanticVisualSearch
from office_assistant.processing.vision import ImageVisionExtractor
from office_assistant.web_evidence.cache import WebSearchService
from office_assistant.web_evidence.image_search import YandexReverseImageSearch
from office_assistant.web_evidence.fetcher import WebContentFetcher
from office_assistant.web_evidence.priority_sites import PrioritySitesService
from office_assistant.web_evidence.providers import (
    SearXNGWebSearchProvider,
    TavilyWebSearchProvider,
)
from office_assistant.web_evidence.service import WebEvidenceService


def bootstrap(settings: Settings | None = None) -> Settings:
    """Load configuration and initialize the application foundation."""

    resolved_settings = settings or load_settings()
    secrets = (
        secret.get_secret_value()
        for secret in (
            resolved_settings.postgres_password,
            resolved_settings.telegram_bot_token,
            resolved_settings.telegram_access_password,
            resolved_settings.openai_api_key,
            resolved_settings.gemini_api_key,
            resolved_settings.tavily_api_key,
            resolved_settings.exa_api_key,
        )
        if secret is not None
    )
    configure_logging(resolved_settings.log_level, secrets)
    logging.getLogger(__name__).info(
        "Office Assistant application starting | environment=%s",
        resolved_settings.app_env,
    )
    return resolved_settings


def main() -> None:
    """Start the configured application process."""

    settings = bootstrap()
    if settings.telegram_polling_enabled:
        if settings.telegram_bot_token is None:
            raise SystemExit("TELEGRAM_BOT_TOKEN is required when polling is enabled")
        # Throwaway engine: a pooled socket must not belong to this loop but to run_polling's.
        asyncio.run(check_startup_clock(settings))
        engine = create_database_engine(settings)
        session_factory = create_session_factory(engine)
        access_password = (
            settings.telegram_access_password.get_secret_value()
            if settings.telegram_access_password is not None
            else None
        )
        storage = LocalFileStorage(settings.file_storage_path)
        ingestion = IngestionSessionService(
            session_factory,
            storage,
            max_files=settings.max_files_per_pending_session,
            ttl_hours=settings.pending_session_ttl_hours,
            max_upload_bytes=settings.max_upload_bytes,
        )
        language_model = create_language_model(settings)
        image_reader = None
        qdrant_store = None
        retrieval = None
        internal_service = None
        web_service = None
        web_clients: list[object] = []
        if language_model is not None:
            image_reader = ImageVisionExtractor(
                language_model,
                max_image_bytes=settings.vision_max_image_bytes,
            )
            if settings.qdrant_url:
                qdrant_store = QdrantKnowledgeStore(
                    settings.qdrant_url,
                    alias=settings.qdrant_collection_alias,
                    collection=settings.qdrant_collection_name,
                )
                retrieval = KnowledgeRetrievalService(
                    qdrant_store,
                    FastEmbedTextAdapter(
                        settings.embedding_model,
                        settings.embedding_cache_path,
                        batch_size=settings.embedding_batch_size,
                    ),
                    FastEmbedSparseAdapter(cache_dir=settings.embedding_cache_path),
                )
            ai_service = GroundedAnswerService(
                session_factory,
                retrieval,
                language_model,
                retrieval_limit=settings.rag_retrieval_limit,
                max_context_tokens=settings.rag_max_context_tokens,
                history_max_messages=settings.chat_history_max_messages,
                history_max_tokens=settings.chat_history_max_tokens,
            )
            registry = BusinessToolRegistry(BusinessQueryService(session_factory))
            internal_service = InternalEvidenceService(
                session_factory,
                registry,
                InternalDocumentSearch(
                    retrieval,
                    DocumentRelevancePolicy(
                        min_hybrid_score=settings.internal_rag_min_score,
                        min_identifier_score=settings.internal_rag_identifier_min_score,
                    ),
                ),
                EvidencePlanner(language_model, OpenAIToolSchemaAdapter(registry)),
                language_model,
                ai_service,
                document_limit=settings.internal_rag_limit,
                source_ttl_minutes=settings.recent_source_context_ttl_minutes,
                source_context_messages=settings.recent_source_context_messages,
                max_tool_calls=settings.internal_tool_max_calls,
            )
            if settings.searxng_base_url:
                searxng = SearXNGWebSearchProvider(
                    settings.searxng_base_url,
                    timeout_seconds=settings.searxng_request_timeout_seconds,
                )
                web_clients.append(searxng)
                tavily = None
                if settings.tavily_api_key is not None:
                    tavily = TavilyWebSearchProvider(
                        settings.tavily_api_key.get_secret_value(),
                        timeout_seconds=settings.tavily_request_timeout_seconds,
                    )
                    web_clients.append(tavily)
                fetcher = WebContentFetcher(
                    timeout_seconds=settings.web_fetch_timeout_seconds,
                    max_bytes=settings.web_fetch_max_bytes,
                    max_redirects=settings.web_fetch_max_redirects,
                    max_text_chars=settings.web_context_max_tokens * 4,
                )
                web_clients.append(fetcher)
                web_service = WebEvidenceService(
                    session_factory,
                    WebSearchService(
                        session_factory,
                        searxng,
                        tavily,
                        stable_ttl_seconds=settings.web_search_cache_ttl_seconds,
                        fresh_ttl_seconds=settings.web_search_fresh_cache_ttl_seconds,
                        priority_sites=PrioritySitesService(session_factory),
                    ),
                    fetcher,
                    language_model,
                    internal_service,
                    default_limit=settings.web_search_default_limit,
                    max_limit=settings.web_search_max_limit,
                    max_fetch_pages=settings.web_fetch_max_pages,
                    context_max_tokens=settings.web_context_max_tokens,
                    source_ttl_minutes=settings.recent_source_context_ttl_minutes,
                    source_context_messages=settings.recent_source_context_messages,
                )
        else:
            ai_service = None
        semantic_visual = (
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
        photo_web = YandexReverseImageSearch()
        web_clients.append(photo_web)
        services = TelegramServices(
            authentication=AuthenticationService(session_factory, access_password),
            users=UserManagementService(session_factory),
            password_state=PasswordEntryState(),
            limiter=PasswordAttemptLimiter(),
            ingestion=ingestion,
            storage=storage,
            session_factory=session_factory,
            ai=ai_service,
            rate_guard=InProcessRateGuard(settings.ai_rate_limit_per_minute),
            telegram_message_limit=settings.telegram_max_message_length,
            extraction_reviews=ExtractionReviewService(session_factory, storage),
            internal_evidence=internal_service,
            knowledge=retrieval,
            file_delivery=SourceFileDeliveryService(
                session_factory,
                storage,
                max_bytes=min(settings.file_delivery_max_bytes, settings.max_upload_bytes),
            ),
            web_evidence=web_service,
            photo_web=photo_web,
            photo_priority_sites=PrioritySitesService(session_factory),
            image_reader=image_reader,
            semantic_visual=semantic_visual,
            reminders=ReminderService(session_factory, clock=SystemClock()),
            reference_images=ReferenceImageService(session_factory, storage),
            sales=SalesLedgerService(session_factory),
            inventory=InventoryLedgerService(session_factory),
            miniapp_url=(
                settings.miniapp_public_origin
                if settings.miniapp_enabled and settings.miniapp_public_origin
                else None
            ),
        )
        application = create_telegram_application(
            settings.telegram_bot_token.get_secret_value(), services
        )
        try:
            application.run_polling()
        finally:
            if language_model is not None:
                asyncio.run(language_model.close())
            if qdrant_store is not None:
                asyncio.run(qdrant_store.close())
            for client in web_clients:
                close = getattr(client, "close", None)
                if close is not None:
                    asyncio.run(close())
            asyncio.run(engine.dispose())
    elif settings.database_check_on_startup:
        asyncio.run(check_database_on_startup(settings))


async def check_database_on_startup(settings: Settings) -> None:
    """Optionally verify PostgreSQL and close resources for smoke checks."""

    engine = create_database_engine(settings)
    try:
        health = await check_database_health(engine)
        if not health.is_healthy:
            logging.getLogger(__name__).error(
                "Database health check failed | detail=%s", health.detail
            )
            raise SystemExit(1)
        logging.getLogger(__name__).info("Database health check passed")
        await check_clock_drift(engine, threshold_seconds=settings.clock_drift_warning_seconds)
    finally:
        await engine.dispose()


async def check_startup_clock(settings: Settings) -> None:
    """Compare this process's clock to the system of record before the first turn is served."""

    engine = create_database_engine(settings)
    try:
        await check_clock_drift(engine, threshold_seconds=settings.clock_drift_warning_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    main()
