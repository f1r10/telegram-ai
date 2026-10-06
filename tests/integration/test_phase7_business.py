import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_events import BusinessEventService
from office_assistant.application.customers import CustomerService
from office_assistant.application.errors import AuthorizationDenied, DomainConflict, EntityNotFound
from office_assistant.application.payments import PaymentService
from office_assistant.application.pricing import PricingService
from office_assistant.application.products import ProductService
from office_assistant.application.proposals import ProposalService
from office_assistant.business.types import (
    PriceResolutionStatus,
    ProposalItemInput,
    ProvenanceInput,
)
from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    BusinessEvent,
    BusinessEventType,
    Customer,
    CustomerAlias,
    FileObject,
    FileObjectState,
    Payment,
    Product,
    ProductAlias,
    ProductPrice,
    Proposal,
    ProposalDocument,
    ProposalDocumentRelation,
    ProposalItem,
    ProposalStatus,
    SourceReference,
    SourceType,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory

pytestmark = pytest.mark.integration

ADMIN_ID = 970001
USER_ID = 970002


def _settings_or_skip() -> Settings:
    settings = load_settings()
    if not all(
        (
            settings.postgres_db,
            settings.postgres_user,
            settings.postgres_password,
            settings.postgres_host,
        )
    ):
        pytest.skip("PostgreSQL integration settings are not configured")
    return settings


async def _reset(settings: Settings) -> None:
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory.begin() as session:
            for model in (
                ProposalItem,
                ProposalDocument,
                Proposal,
                BusinessEvent,
                Payment,
                ProductPrice,
                CustomerAlias,
                ProductAlias,
                Customer,
                Product,
                SourceReference,
            ):
                await session.execute(delete(model))
            actor_ids = select(User.id).where(User.telegram_user_id.in_([ADMIN_ID, USER_ID]))
            await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(actor_ids)))
            await session.execute(delete(FileObject).where(FileObject.storage_key.like("phase7/%")))
            await session.execute(
                delete(User).where(User.telegram_user_id.in_([ADMIN_ID, USER_ID]))
            )
    finally:
        await engine.dispose()


async def _actors(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory.begin() as session:
        session.add_all(
            [
                User(
                    telegram_user_id=ADMIN_ID,
                    role=UserRole.ADMIN,
                    status=UserStatus.ACTIVE,
                ),
                User(
                    telegram_user_id=USER_ID,
                    role=UserRole.USER,
                    status=UserStatus.ACTIVE,
                ),
            ]
        )


def test_phase7_domain_queries_audit_and_security() -> None:
    settings = _settings_or_skip()
    asyncio.run(_reset(settings))

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        await _actors(factory)
        products = ProductService(factory)
        customers = CustomerService(factory)
        pricing = PricingService(factory)
        payments = PaymentService(factory)
        events = BusinessEventService(factory)
        proposals = ProposalService(factory)
        source = ProvenanceInput(
            SourceType.TELEGRAM_USER,
            locator={"chat_id": "synthetic", "message_id": 42},
            description="Synthetic Phase 7 test",
        )
        try:
            product = await products.create_product(
                USER_ID, display_code="KA-45", name="Synthetic Product"
            )
            await products.add_alias(USER_ID, product.id, "Main pump")
            assert (await products.find_product_by_code("ka 45")).id == product.id  # type: ignore[union-attr]
            assert (await products.search_product_by_alias("MAIN PUMP")).id == product.id  # type: ignore[union-attr]

            customer = await customers.create_customer(USER_ID, display_name="ABC   MMC")
            await customers.add_alias(USER_ID, customer.id, "ABC Company")
            assert (await customers.find_customer_by_name("abc mmc")).id == customer.id  # type: ignore[union-attr]
            assert (await customers.search_customer_by_alias("abc company")).id == customer.id  # type: ignore[union-attr]

            start = datetime(2026, 1, 1, tzinfo=UTC)
            first = await pricing.add_price(
                USER_ID,
                product_id=product.id,
                amount=Decimal("100.1250"),
                currency="azn",
                effective_at=start,
                provenance=source,
            )
            specific = await pricing.add_price(
                USER_ID,
                product_id=product.id,
                customer_id=customer.id,
                amount=Decimal("90.5000"),
                currency="AZN",
                effective_at=start,
                provenance=source,
            )
            general_result = await pricing.resolve_current_price(product.id, at=start)
            customer_result = await pricing.resolve_current_price(
                product.id, customer_id=customer.id, at=start
            )
            assert general_result.status == PriceResolutionStatus.FOUND
            assert general_result.price is not None
            assert general_result.price.amount == Decimal("100.1250")
            assert customer_result.price is not None
            assert customer_result.price.id == specific.id
            assert (
                await pricing.resolve_current_price(product.id, at=start - timedelta(days=1))
            ).status == PriceResolutionStatus.NOT_FOUND

            conflict_at = start + timedelta(days=30)
            conflict_a = await pricing.add_price(
                USER_ID,
                product_id=product.id,
                amount=Decimal("110.0000"),
                currency="AZN",
                effective_at=conflict_at,
                provenance=source,
            )
            conflict_b = await pricing.add_price(
                USER_ID,
                product_id=product.id,
                amount=Decimal("111.0000"),
                currency="AZN",
                effective_at=conflict_at,
                provenance=source,
            )
            conflict = await pricing.resolve_current_price(product.id, at=conflict_at)
            assert conflict.status == PriceResolutionStatus.CONFLICT
            assert {item.id for item in conflict.conflicting_prices} == {
                conflict_a.id,
                conflict_b.id,
            }
            await pricing.supersede_price(ADMIN_ID, conflict_a.id, conflict_b.id)
            resolved = await pricing.resolve_current_price(product.id, at=conflict_at)
            assert resolved.status == PriceResolutionStatus.FOUND
            assert resolved.price is not None and resolved.price.id == conflict_b.id
            history = await pricing.get_price_history(product.id)
            assert [item.id for item in history] == [first.id, conflict_a.id, conflict_b.id]

            await payments.create_payment(
                USER_ID,
                customer_id=customer.id,
                amount=Decimal("25.1250"),
                currency="AZN",
                paid_at=start,
                provenance=source,
            )
            await payments.create_payment(
                USER_ID,
                customer_id=customer.id,
                amount=Decimal("10.0000"),
                currency="USD",
                paid_at=start + timedelta(days=1),
                provenance=source,
            )
            assert await payments.get_customer_payment_total(customer.id) == {
                "AZN": Decimal("25.1250"),
                "USD": Decimal("10.0000"),
            }
            assert (
                len(
                    await payments.get_customer_payments(
                        customer.id, from_date=start + timedelta(hours=12)
                    )
                )
                == 1
            )

            event = await events.create_event(
                USER_ID,
                customer_id=customer.id,
                event_type=BusinessEventType.MEETING,
                occurred_at=start,
                summary="Synthetic meeting",
                provenance=source,
            )
            assert (await events.get_customer_events(customer.id))[0].id == event.id

            async with factory.begin() as session:
                file = FileObject(
                    storage_key="phase7/synthetic-proposal.pdf",
                    original_filename="synthetic-proposal.pdf",
                    content_type="application/pdf",
                    byte_size=1,
                    sha256="7" * 64,
                    state=FileObjectState.ACTIVE,
                )
                session.add(file)
                await session.flush()
                file_id = file.id
            proposal = await proposals.create_proposal(
                USER_ID,
                customer_id=customer.id,
                proposal_date=date(2026, 2, 1),
                status=ProposalStatus.SENT,
                proposal_number="SYN-001",
                provenance=source,
                documents=[(file_id, ProposalDocumentRelation.PRIMARY)],
                items=[
                    ProposalItemInput(
                        position=1,
                        product_id=product.id,
                        raw_product_name="KA 45 source label",
                        quantity=Decimal("2.0000"),
                        unit_price=Decimal("111.0000"),
                        currency="AZN",
                        line_total=Decimal("222.0000"),
                        provenance=source,
                    ),
                    ProposalItemInput(
                        position=2,
                        raw_product_name="Unresolved synthetic item",
                        provenance=source,
                    ),
                ],
            )
            assert (await proposals.get_latest_customer_proposal(customer.id)).id == proposal.id  # type: ignore[union-attr]
            assert len(await proposals.get_customer_proposals(customer.id)) == 1
            proposal_items = await proposals.get_proposal_items(proposal.id)
            assert proposal_items[0].product_id == product.id
            assert proposal_items[0].raw_product_name == "KA 45 source label"
            assert proposal_items[1].product_id is None
            assert proposal_items[1].raw_product_name == "Unresolved synthetic item"

            with pytest.raises(AuthorizationDenied):
                await products.deactivate_product(USER_ID, product.id)
            deactivated = await products.deactivate_product(ADMIN_ID, product.id)
            assert not deactivated.is_active
            assert await products.find_product_by_code("KA45") is None
            assert (await products.get_product(product.id)).id == product.id  # type: ignore[union-attr]

            async with factory() as session:
                provenance = await session.get(SourceReference, first.source_reference_id)
                assert provenance is not None
                assert provenance.locator == {"chat_id": "synthetic", "message_id": 42}
                actions = set((await session.execute(select(AuditEvent.action))).scalars().all())
                assert {
                    "PRODUCT_CREATED",
                    "PRODUCT_ALIAS_ADDED",
                    "CUSTOMER_CREATED",
                    "CUSTOMER_ALIAS_ADDED",
                    "PRICE_ADDED",
                    "PRICE_SUPERSEDED",
                    "PAYMENT_CREATED",
                    "BUSINESS_EVENT_CREATED",
                    "PROPOSAL_CREATED",
                    "PRODUCT_DEACTIVATED",
                } <= actions
        finally:
            await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_reset(settings))


def test_phase7_concurrent_identity_conflicts() -> None:
    settings = _settings_or_skip()
    asyncio.run(_reset(settings))

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        await _actors(factory)
        products = ProductService(factory)
        customers = CustomerService(factory)
        try:
            product_results = await asyncio.gather(
                products.create_product(USER_ID, display_code="KA-46", name="One"),
                products.create_product(USER_ID, display_code="ka 46", name="Two"),
                return_exceptions=True,
            )
            assert sum(isinstance(item, Product) for item in product_results) == 1
            assert sum(isinstance(item, DomainConflict) for item in product_results) == 1
            first = await products.create_product(USER_ID, display_code="KA47", name="First")
            second = await products.create_product(USER_ID, display_code="KA48", name="Second")
            alias_results = await asyncio.gather(
                products.add_alias(USER_ID, first.id, "shared product"),
                products.add_alias(USER_ID, second.id, "SHARED   PRODUCT"),
                return_exceptions=True,
            )
            assert sum(isinstance(item, ProductAlias) for item in alias_results) == 1
            assert sum(isinstance(item, DomainConflict) for item in alias_results) == 1

            first_customer = await customers.create_customer(USER_ID, display_name="ABC MMC")
            second_customer = await customers.create_customer(USER_ID, display_name="XYZ MMC")
            customer_alias_results = await asyncio.gather(
                customers.add_alias(USER_ID, first_customer.id, "Common Client"),
                customers.add_alias(USER_ID, second_customer.id, "common   client"),
                return_exceptions=True,
            )
            assert sum(isinstance(item, CustomerAlias) for item in customer_alias_results) == 1
            assert sum(isinstance(item, DomainConflict) for item in customer_alias_results) == 1
        finally:
            await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_reset(settings))


def test_phase7_business_provenance_audit_transaction_rolls_back() -> None:
    settings = _settings_or_skip()
    asyncio.run(_reset(settings))

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        await _actors(factory)
        customer = await CustomerService(factory).create_customer(
            USER_ID, display_name="Rollback MMC"
        )
        service = ProposalService(factory)
        source = ProvenanceInput(SourceType.TELEGRAM_USER, description="rollback test")
        try:
            async with factory() as session:
                before_sources = await session.scalar(select(func.count(SourceReference.id)))
                before_proposals = await session.scalar(select(func.count(Proposal.id)))
                before_audits = await session.scalar(select(func.count(AuditEvent.id)))
            with pytest.raises(EntityNotFound):
                await service.create_proposal(
                    USER_ID,
                    customer_id=customer.id,
                    proposal_date=date(2026, 1, 1),
                    status=ProposalStatus.DRAFT,
                    provenance=source,
                    documents=[(999999999, ProposalDocumentRelation.PRIMARY)],
                )
            async with factory() as session:
                assert (
                    await session.scalar(select(func.count(SourceReference.id))) == before_sources
                )
                assert await session.scalar(select(func.count(Proposal.id))) == before_proposals
                assert await session.scalar(select(func.count(AuditEvent.id))) == before_audits
        finally:
            await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_reset(settings))
