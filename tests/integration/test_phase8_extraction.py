import asyncio
import re
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.ai.provider import ModelResponse, TokenUsage
from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.ingestion import IngestionSessionService
from office_assistant.core.settings import Settings, load_settings
from office_assistant.extraction.review import ExtractionReviewService
from office_assistant.extraction.schemas import StructuredExtractionResult
from office_assistant.extraction.service import StructuredExtractionService
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AIUsage,
    AuditEvent,
    CandidateStatus,
    Customer,
    ExtractionReviewStatus,
    IngestionJob,
    IngestionJobStatus,
    IngestionSession,
    IngestionSessionItem,
    KnowledgeIndexJob,
    KnowledgeIndexJobStatus,
    Payment,
    Product,
    ProductPrice,
    Proposal,
    ProposalDocument,
    ProposalItem,
    SourceReference,
    StructuredExtractionBatch,
    StructuredExtractionCandidate,
    StructuredExtractionJob,
    StructuredExtractionRun,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.infrastructure.storage.local import LocalFileStorage, StorageError

pytestmark = pytest.mark.integration


class _FakeExtractionModel:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, **kwargs: object) -> ModelResponse:
        self.calls += 1
        messages = cast(list[dict[str, str]], kwargs["input_messages"])
        source_id = re.search(r"\[SOURCE ([^]]+)]", messages[0]["content"])
        assert source_id is not None
        source = source_id.group(1)
        output = StructuredExtractionResult.model_validate(
            {
                "candidates": [
                    {
                        "candidate_type": "PRODUCT",
                        "local_ref": "P1",
                        "product_code": "KA-45",
                        "name": "Synthetic KA45",
                        "source_refs": [source],
                        "confidence": 0.99,
                    },
                    {
                        "candidate_type": "CUSTOMER",
                        "local_ref": "C1",
                        "display_name": "ABC MMC",
                        "source_refs": [source],
                    },
                    {
                        "candidate_type": "PRICE",
                        "product_ref": "P1",
                        "customer_ref": "C1",
                        "amount": "125.0000",
                        "currency": "AZN",
                        "effective_at": "2026-09-05T00:00:00+04:00",
                        "price_kind": "CUSTOMER_SPECIFIC",
                        "price_role": "SELLING_PRICE",
                        "source_refs": [source],
                    },
                    {
                        "candidate_type": "PAYMENT",
                        "customer_ref": "C1",
                        "amount": "1200.0000",
                        "currency": "AZN",
                        "paid_at": "2026-09-05T00:00:00+04:00",
                        "source_refs": [source],
                    },
                    {
                        "candidate_type": "PROPOSAL",
                        "local_ref": "O1",
                        "customer_ref": "C1",
                        "proposal_date": "2026-09-05",
                        "status": "SENT",
                        "source_refs": [source],
                    },
                    {
                        "candidate_type": "PROPOSAL_ITEM",
                        "proposal_ref": "O1",
                        "position": 1,
                        "product_ref": "P1",
                        "raw_product_name": "KA 45",
                        "quantity": "2",
                        "unit_price": "125",
                        "currency": "AZN",
                        "line_total": "250",
                        "source_refs": [source],
                    },
                    {
                        "candidate_type": "PRICE",
                        "product_ref": "P1",
                        "amount": "999.0000",
                        "currency": "AZN",
                        "price_kind": "GENERAL",
                        "price_role": "UNKNOWN",
                        "source_refs": ["S99"],
                    },
                    {
                        "candidate_type": "PRODUCT",
                        "local_ref": "P1",
                        "product_code": "KA-45",
                        "name": "Synthetic KA45",
                        "source_refs": [source],
                        "confidence": 0.99,
                    },
                ]
            }
        )
        return ModelResponse(
            output, "fake-luna", f"response-{self.calls}", TokenUsage(100, 5, 40, 140), 7
        )


def _settings_or_skip() -> Settings:
    settings = load_settings()
    if not settings.postgres_password or not settings.postgres_host:
        pytest.skip("PostgreSQL integration settings are not configured")
    return settings


async def _cleanup(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory.begin() as session:
        for model in (
            AIUsage,
            StructuredExtractionCandidate,
            StructuredExtractionBatch,
            StructuredExtractionRun,
            StructuredExtractionJob,
            ProposalItem,
            ProposalDocument,
            Proposal,
            Payment,
            ProductPrice,
            Customer,
            Product,
            SourceReference,
            AuditEvent,
            KnowledgeIndexJob,
            IngestionJob,
            IngestionSessionItem,
            IngestionSession,
            User,
        ):
            await session.execute(delete(model))


def test_text_only_extraction_review_commit_is_authorized_and_idempotent(tmp_path: Path) -> None:
    settings = _settings_or_skip()

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        await _cleanup(factory)
        storage = LocalFileStorage(tmp_path)
        try:
            async with factory.begin() as session:
                owner = User(telegram_user_id=980001, role=UserRole.USER, status=UserStatus.ACTIVE)
                other = User(telegram_user_id=980002, role=UserRole.USER, status=UserStatus.ACTIVE)
                admin = User(telegram_user_id=980003, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
                session.add_all([owner, other, admin])
                await session.flush()
            ingestion = IngestionSessionService(factory, storage)
            assert await ingestion.add_instruction(
                owner,
                text_content="ABC MMC üçün KA45 qiyməti 125 AZN razılaşdırıldı.",
                chat_id=123,
                message_id=456,
                update_id=789,
                create_session=True,
            )
            summary = await ingestion.commit(owner)
            assert (
                summary is not None and summary.file_count == 0 and summary.instruction_count == 1
            )
            async with factory.begin() as session:
                assert summary.job_id is not None
                await session.execute(
                    update(IngestionJob)
                    .where(IngestionJob.id == summary.job_id)
                    .values(status=IngestionJobStatus.SUCCEEDED)
                )
                session.add(
                    KnowledgeIndexJob(
                        ingestion_job_id=summary.job_id,
                        status=KnowledgeIndexJobStatus.SUCCEEDED,
                        collection_alias="test",
                        collection_name="test-v1",
                        embedding_model="test",
                        chunking_version="test",
                    )
                )
                job = StructuredExtractionJob(
                    ingestion_session_id=summary.session_id,
                    requested_by_user_id=owner.id,
                    status="PENDING",
                )
                session.add(job)
                await session.flush()
                job_id = job.id
            model = _FakeExtractionModel()
            extractor = StructuredExtractionService(
                factory, storage, model, model="fake-luna", max_chars=2000, batch_size=30
            )
            claim = await extractor.claim_next("test-worker")
            assert claim is not None and claim.job_id == job_id
            await extractor.process(claim)
            async with factory() as session:
                run = await session.scalar(
                    select(StructuredExtractionRun).where(StructuredExtractionRun.job_id == job_id)
                )
                assert run is not None
                run_id = run.id
                assert run.candidate_count == 7
                assert await session.scalar(select(func.count(AIUsage.id))) == 1
                assert await session.scalar(select(func.sum(AIUsage.total_tokens))) == 140
            reviews = ExtractionReviewService(factory, storage)
            with pytest.raises(AuthorizationDenied):
                await reviews.approve(other.telegram_user_id, run_id)
            assert (await reviews.summary(admin.telegram_user_id, run_id))["total"] == 7
            original_apply = reviews._apply_safe  # pyright: ignore[reportPrivateUsage]
            aborted = AsyncMock(side_effect=StorageError("storage unavailable"))
            reviews._apply_safe = aborted  # pyright: ignore[reportPrivateUsage]
            with pytest.raises(StorageError):
                await reviews.approve(owner.telegram_user_id, run_id)
            assert aborted.await_count == 1
            async with factory() as session:
                stranded = await session.get(StructuredExtractionRun, run_id)
                assert stranded is not None
                assert stranded.review_status == ExtractionReviewStatus.PARTIAL
            reviews._apply_safe = original_apply  # pyright: ignore[reportPrivateUsage]
            await asyncio.gather(
                reviews.approve(owner.telegram_user_id, run_id),
                reviews.approve(owner.telegram_user_id, run_id),
            )
            result = await reviews.approve(owner.telegram_user_id, run_id)
            assert result.applied == 6
            assert result.needs_admin == 1
            repeated = await reviews.approve(owner.telegram_user_id, run_id)
            assert repeated == result
            async with factory() as session:
                assert await session.scalar(select(func.count(Product.id))) == 1
                assert await session.scalar(select(func.count(Customer.id))) == 1
                assert await session.scalar(select(func.count(ProductPrice.id))) == 1
                assert await session.scalar(select(func.count(Payment.id))) == 1
                assert await session.scalar(select(func.count(Proposal.id))) == 1
                assert await session.scalar(select(func.count(ProposalItem.id))) == 1
                statuses = set(
                    (await session.execute(select(StructuredExtractionCandidate.status)))
                    .scalars()
                    .all()
                )
                assert statuses == {CandidateStatus.APPLIED, CandidateStatus.NEEDS_REVIEW}
        finally:
            await _cleanup(factory)
            await engine.dispose()

    asyncio.run(scenario())
