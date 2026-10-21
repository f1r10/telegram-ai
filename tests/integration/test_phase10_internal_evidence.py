import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.ai.provider import LanguageModel, ModelResponse, TokenUsage
from office_assistant.ai.schemas import GroundedModelOutput
from office_assistant.ai.service import ChatAnswer, GroundedAnswerService
from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.pricing import PricingService
from office_assistant.application.products import ProductService
from office_assistant.business.types import ProvenanceInput
from office_assistant.business_tools.models import ToolResult, ToolStatus
from office_assistant.business_tools.registry import BusinessToolRegistry
from office_assistant.business_tools.service import BusinessQueryService
from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AIConversation,
    AIMessage,
    AIMessageSource,
    AuditEvent,
    FileObject,
    FileObjectState,
    Product,
    ProductPrice,
    SourceReference,
    SourceType,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.internal_evidence.delivery import (
    FileDeliveryAmbiguous,
    FileDeliveryError,
    SourceFileDeliveryService,
)
from office_assistant.internal_evidence.document_search import InternalDocumentSearch
from office_assistant.internal_evidence.models import RoutingCategory
from office_assistant.internal_evidence.router import EvidencePlanner
from office_assistant.internal_evidence.service import (
    NOT_FOUND_INTERNAL_AZ,
    InternalEvidenceService,
)
from office_assistant.internal_evidence.tool_adapter import OpenAIToolSchemaAdapter
from office_assistant.knowledge.embeddings import FastEmbedSparseAdapter, FastEmbedTextAdapter
from office_assistant.knowledge.models import DocumentChunk
from office_assistant.knowledge.qdrant_store import QdrantKnowledgeStore
from office_assistant.knowledge.retrieval import KnowledgeRetrievalService

pytestmark = pytest.mark.integration
ACTOR_TELEGRAM_ID = 991001
OTHER_TELEGRAM_ID = 991002
INACTIVE_TELEGRAM_ID = 991003


class _DenseEmbedder:
    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class _SparseEmbedder:
    def embed(self, texts: Sequence[str]) -> list[tuple[list[int], list[float]]]:
        return [([1], [1.0]) for _ in texts]


class _SynthesisModel:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate(self, **kwargs: object) -> ModelResponse:
        request_type = str(kwargs["request_type"])
        self.calls.append(request_type)
        assert request_type == "internal_synthesis"
        return ModelResponse(
            parsed=GroundedModelOutput(
                answer=(
                    "Biznes bazasında KA45 qiyməti 125 AZN-dir [E1]. "
                    "Sənəddə 110 AZN göstərilir [E2]."
                ),
                citations=["E1", "E2"],
                conflict_detected=True,
            ),
            model="test-model",
            response_id="phase10-response",
            usage=TokenUsage(input_tokens=20, output_tokens=10, total_tokens=30),
            latency_ms=1,
        )


class _GeneralService:
    async def answer_general(self, user_id: int, question: str) -> ChatAnswer:
        return ChatAnswer("Bakı.", "test-model", TokenUsage(total_tokens=1))


class _EmptyDocumentSearch:
    async def search(self, value: object) -> list[object]:
        return []


class _AmbiguousRegistry:
    names = ("resolve_product_price",)

    async def execute(self, name: str, context: object, payload: object) -> ToolResult[object]:
        return ToolResult[object](
            status=ToolStatus.AMBIGUOUS,
            data={
                "candidates": [
                    {"display_code": "KA45-A"},
                    {"display_code": "KA45-B"},
                ]
            },
        )


def _settings_or_skip() -> Settings:
    settings = load_settings()
    if not settings.postgres_host or not settings.postgres_password:
        pytest.skip("PostgreSQL integration settings are not configured")
    return settings


def _write_active_blob(storage: LocalFileStorage, filename: str, content: bytes) -> tuple[str, str]:
    staging_path, staging_key = storage.create_staging_path(Path(filename).suffix)
    staging_path.write_bytes(content)
    return storage.activate(staging_key), sha256(content).hexdigest()


async def _cleanup(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory.begin() as session:
        actor_ids = select(User.id).where(
            User.telegram_user_id.in_([ACTOR_TELEGRAM_ID, OTHER_TELEGRAM_ID, INACTIVE_TELEGRAM_ID])
        )
        await session.execute(delete(AIConversation).where(AIConversation.user_id.in_(actor_ids)))
        await session.execute(
            delete(ProductPrice).where(ProductPrice.created_by_user_id.in_(actor_ids))
        )
        await session.execute(delete(Product).where(Product.created_by_user_id.in_(actor_ids)))
        await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(actor_ids)))
        await session.execute(
            delete(SourceReference).where(SourceReference.actor_user_id.in_(actor_ids))
        )
        await session.execute(
            delete(FileObject).where(
                FileObject.original_filename.in_(["../price.xlsx", "../catalog.pdf"])
            )
        )
        await session.execute(delete(User).where(User.id.in_(actor_ids)))


def test_real_postgres_qdrant_hybrid_conflict_and_secure_file_follow_up(
    tmp_path: Path,
) -> None:
    settings = _settings_or_skip()
    if not settings.qdrant_url:
        pytest.skip("QDRANT_URL is not configured for integration tests")
    collection = f"phase10_test_{uuid4().hex[:10]}"
    store = QdrantKnowledgeStore(
        settings.qdrant_url,
        alias=collection,
        collection=f"{collection}_v1",
    )

    async def scenario() -> None:
        if not await store.health_check():
            pytest.skip(f"Qdrant is not reachable at {settings.qdrant_url}")
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        storage = LocalFileStorage(tmp_path / "storage")
        await _cleanup(factory)
        try:
            price_bytes = b"KA45,125 AZN\n"
            document_bytes = b"%PDF synthetic original: KA45 110 AZN"
            price_key, price_hash = _write_active_blob(storage, "price.xlsx", price_bytes)
            document_key, document_hash = _write_active_blob(storage, "catalog.pdf", document_bytes)
            async with factory.begin() as session:
                actor = User(
                    telegram_user_id=ACTOR_TELEGRAM_ID,
                    role=UserRole.USER,
                    status=UserStatus.ACTIVE,
                )
                other = User(
                    telegram_user_id=OTHER_TELEGRAM_ID,
                    role=UserRole.USER,
                    status=UserStatus.ACTIVE,
                )
                inactive = User(
                    telegram_user_id=INACTIVE_TELEGRAM_ID,
                    role=UserRole.USER,
                    status=UserStatus.DISABLED,
                )
                price_file = FileObject(
                    storage_key=price_key,
                    original_filename="../price.xlsx",
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    byte_size=len(price_bytes),
                    sha256=price_hash,
                    state=FileObjectState.ACTIVE,
                )
                document_file = FileObject(
                    storage_key=document_key,
                    original_filename="../catalog.pdf",
                    content_type="application/pdf",
                    byte_size=len(document_bytes),
                    sha256=document_hash,
                    state=FileObjectState.ACTIVE,
                )
                session.add_all([actor, other, inactive, price_file, document_file])
                await session.flush()
                actor_id = actor.id
                document_file_id = document_file.id
                price_file_id = price_file.id

            provenance = ProvenanceInput(
                SourceType.FILE,
                file_id=price_file_id,
                locator={"sheet": "Prices", "row": 2},
            )
            product = await ProductService(factory).create_product(
                ACTOR_TELEGRAM_ID,
                display_code="KA45",
                name="Phase 10 product",
                provenance=provenance,
            )
            await PricingService(factory).add_price(
                ACTOR_TELEGRAM_ID,
                product_id=product.id,
                amount=Decimal("125"),
                currency="AZN",
                effective_at=datetime(2026, 1, 1, tzinfo=UTC),
                provenance=provenance,
            )

            await store.ensure_collection(3)
            chunk = DocumentChunk(
                uuid4().hex,
                document_file_id,
                1,
                1,
                "KA45 qiyməti 110 AZN-dir.",
                "KA45 qiyməti 110 AZN-dir.",
                ("paragraph",),
                {"page": 1},
            )
            unrelated = DocumentChunk(
                uuid4().hex,
                document_file_id,
                1,
                2,
                "Dahua XVR məhsulları üçün əlaqəsiz qiymət təklifi.",
                "Dahua XVR məhsulları üçün əlaqəsiz qiymət təklifi.",
                ("paragraph",),
                {"page": 2},
            )
            await store.upsert_chunks(
                [chunk, unrelated],
                [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
                [([1], [1.0]), ([1], [1.0])],
                source_filename="catalog.pdf",
                media_type="application/pdf",
                embedding_model="phase10-test",
            )
            retrieval = KnowledgeRetrievalService(
                store,
                cast(FastEmbedTextAdapter, _DenseEmbedder()),
                cast(FastEmbedSparseAdapter, _SparseEmbedder()),
            )
            registry = BusinessToolRegistry(BusinessQueryService(factory))
            model = _SynthesisModel()
            language_model = cast(LanguageModel, model)
            service = InternalEvidenceService(
                factory,
                registry,
                InternalDocumentSearch(retrieval),
                EvidencePlanner(language_model, OpenAIToolSchemaAdapter(registry)),
                language_model,
                cast(GroundedAnswerService, _GeneralService()),
            )
            actor = User(
                id=actor_id,
                telegram_user_id=ACTOR_TELEGRAM_ID,
                role=UserRole.USER,
                status=UserStatus.ACTIVE,
            )
            answer = await service.answer(actor, "KA45-in qiyməti nədir?")
            assert answer.route == RoutingCategory.INTERNAL_HYBRID
            assert answer.tool_calls == 1
            assert "125 AZN" in answer.answer and "110 AZN" in answer.answer
            assert "fərq" in answer.answer
            assert [item.source_class for item in answer.citations] == [
                "STRUCTURED_AUTHORITATIVE",
                "INTERNAL_DOCUMENT",
            ]
            assert {item.file_id for item in answer.citations} == {
                price_file_id,
                document_file_id,
            }
            assert [item.conflict_type for item in answer.conflicts] == ["STRUCTURED_VS_DOCUMENT"]
            assert model.calls == ["internal_synthesis"]

            async with factory() as session:
                mappings = list(
                    (
                        await session.execute(
                            select(AIMessageSource).order_by(AIMessageSource.citation_index)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert [(item.citation_index, item.file_id) for item in mappings] == [
                    (1, price_file_id),
                    (2, document_file_id),
                ]

            delivery = SourceFileDeliveryService(factory, storage, max_bytes=1024 * 1024)
            with pytest.raises(FileDeliveryAmbiguous):
                await delivery.resolve_recent(ACTOR_TELEGRAM_ID, None)
            selected = await delivery.resolve_recent(ACTOR_TELEGRAM_ID, 2)
            assert selected.filename == "catalog.pdf"
            assert selected.path.read_bytes() == document_bytes
            assert selected.file_id == document_file_id
            structured_file = await delivery.resolve_recent(ACTOR_TELEGRAM_ID, 1)
            assert structured_file.filename == "price.xlsx"
            assert structured_file.path.read_bytes() == price_bytes
            # A Mini App ticket pins the original file as well as its citation number.
            pinned = await delivery.resolve_recent(
                ACTOR_TELEGRAM_ID, 2, expected_file_id=document_file_id
            )
            assert pinned.file_id == document_file_id
            with pytest.raises(FileDeliveryError):
                await delivery.resolve_recent(ACTOR_TELEGRAM_ID, 2, expected_file_id=price_file_id)
            with pytest.raises(FileDeliveryError):
                await delivery.resolve_recent(
                    OTHER_TELEGRAM_ID, 2, expected_file_id=document_file_id
                )

            # Another answer reuses citation 2 for a DIFFERENT original. The
            # unpinned Telegram command resolves the new answer, but the old
            # Mini App ticket must retain its own original or fail closed.
            async with factory.begin() as session:
                conversation = await session.scalar(
                    select(AIConversation).where(
                        AIConversation.user_id == actor_id,
                        AIConversation.mode == "internal",
                    )
                )
                assert conversation is not None
                newer = AIMessage(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="Later answer",
                    created_at=datetime.now(UTC) + timedelta(minutes=1),
                )
                session.add(newer)
                await session.flush()
                newer_id = newer.id
                session.add(
                    AIMessageSource(
                        message_id=newer.id,
                        citation_index=2,
                        evidence_id="newer-citation",
                        source_type="INTERNAL_DOCUMENT",
                        file_id=price_file_id,
                        expires_at=datetime.now(UTC) + timedelta(minutes=30),
                    )
                )
            assert (await delivery.resolve_recent(ACTOR_TELEGRAM_ID, 2)).file_id == price_file_id
            assert (
                await delivery.resolve_recent(
                    ACTOR_TELEGRAM_ID, 2, expected_file_id=document_file_id
                )
            ).file_id == document_file_id
            async with factory.begin() as session:
                await session.execute(
                    delete(AIMessageSource).where(AIMessageSource.message_id == newer_id)
                )
                await session.execute(delete(AIMessage).where(AIMessage.id == newer_id))

            with pytest.raises(FileDeliveryError):
                await delivery.resolve_recent(OTHER_TELEGRAM_ID, 2)
            with pytest.raises(AuthorizationDenied):
                await delivery.resolve_recent(INACTIVE_TELEGRAM_ID, 2)

            no_source_service = InternalEvidenceService(
                factory,
                registry,
                cast(InternalDocumentSearch, _EmptyDocumentSearch()),
                EvidencePlanner(language_model, OpenAIToolSchemaAdapter(registry)),
                language_model,
                cast(GroundedAnswerService, _GeneralService()),
            )
            absent = await no_source_service.answer(actor, "ZZ99 qiyməti nədir?")
            assert absent.answer == NOT_FOUND_INTERNAL_AZ
            assert absent.insufficient_evidence
            assert model.calls == ["internal_synthesis"]

            ambiguous_registry = cast(BusinessToolRegistry, _AmbiguousRegistry())
            ambiguous_service = InternalEvidenceService(
                factory,
                ambiguous_registry,
                cast(InternalDocumentSearch, _EmptyDocumentSearch()),
                EvidencePlanner(language_model, OpenAIToolSchemaAdapter(ambiguous_registry)),
                language_model,
                cast(GroundedAnswerService, _GeneralService()),
            )
            ambiguous = await ambiguous_service.answer(actor, "KA45-in qiyməti nədir?")
            assert ambiguous.route == RoutingCategory.CLARIFICATION_REQUIRED
            assert "dəqiqləşdirin" in ambiguous.answer
            assert "KA45-A" in ambiguous.answer and "KA45-B" in ambiguous.answer
            assert model.calls == ["internal_synthesis"]

            general = await service.answer(actor, "Azərbaycanın paytaxtı haradır?")
            assert general.route == RoutingCategory.GENERAL_CHAT
            assert general.answer == "Bakı."
            assert model.calls == ["internal_synthesis"]

            selected.path.unlink()
            with pytest.raises(FileDeliveryError):
                await delivery.resolve_recent(ACTOR_TELEGRAM_ID, 2)
            async with factory.begin() as session:
                await session.execute(
                    update(FileObject)
                    .where(FileObject.id == document_file_id)
                    .values(storage_key="../outside.pdf")
                )
            with pytest.raises(FileDeliveryError):
                await delivery.resolve_recent(ACTOR_TELEGRAM_ID, 2)
        finally:
            try:
                await store.client.delete_collection(store.collection)
            except Exception:
                pass
            await _cleanup(factory)
            await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(store.close())
