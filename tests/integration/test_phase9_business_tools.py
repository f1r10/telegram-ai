import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_events import BusinessEventService
from office_assistant.application.customers import CustomerService
from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.payments import PaymentService
from office_assistant.application.pricing import PricingService
from office_assistant.application.products import ProductService
from office_assistant.application.proposals import ProposalService
from office_assistant.business.normalization import normalize_customer_name, normalize_product_code
from office_assistant.business.types import ProposalItemInput, ProvenanceInput
from office_assistant.business_tools.models import ToolExecutionContext
from office_assistant.business_tools.registry import BusinessToolRegistry
from office_assistant.business_tools.service import BusinessQueryService
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

ADMIN_ID = 990001
USER_ID = 990002
PENDING_ID = 990003
DISABLED_ID = 990004
REJECTED_ID = 990005


def _settings_or_skip() -> Settings:
    settings = load_settings()
    if not settings.postgres_password or not settings.postgres_host:
        pytest.skip("PostgreSQL integration settings are not configured")
    return settings


async def _cleanup(factory: async_sessionmaker[AsyncSession]) -> None:
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
        ids = [ADMIN_ID, USER_ID, PENDING_ID, DISABLED_ID, REJECTED_ID]
        actor_ids = select(User.id).where(User.telegram_user_id.in_(ids))
        await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(actor_ids)))
        await session.execute(delete(FileObject).where(FileObject.storage_key.like("phase9/%")))
        await session.execute(delete(User).where(User.telegram_user_id.in_(ids)))


def _body(value: object) -> dict[str, object]:
    assert hasattr(value, "model_dump")
    return value.model_dump(mode="json")  # type: ignore[union-attr,no-any-return]


def test_phase9_registry_queries_are_typed_authorized_and_deterministic() -> None:
    settings = _settings_or_skip()

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        await _cleanup(factory)
        try:
            async with factory.begin() as session:
                actors = [
                    User(telegram_user_id=ADMIN_ID, role=UserRole.ADMIN, status=UserStatus.ACTIVE),
                    User(telegram_user_id=USER_ID, role=UserRole.USER, status=UserStatus.ACTIVE),
                    User(
                        telegram_user_id=PENDING_ID, role=UserRole.USER, status=UserStatus.PENDING
                    ),
                    User(
                        telegram_user_id=DISABLED_ID,
                        role=UserRole.USER,
                        status=UserStatus.DISABLED,
                    ),
                    User(
                        telegram_user_id=REJECTED_ID,
                        role=UserRole.USER,
                        status=UserStatus.REJECTED,
                    ),
                ]
                session.add_all(actors)
                await session.flush()
                actor_ids = {item.telegram_user_id: item.id for item in actors}
            user_context = ToolExecutionContext(
                user_id=actor_ids[USER_ID], role="USER", request_id="phase9-user"
            )
            admin_context = ToolExecutionContext(
                user_id=actor_ids[ADMIN_ID], role="ADMIN", request_id="phase9-admin"
            )
            products = ProductService(factory)
            customers = CustomerService(factory)
            pricing = PricingService(factory)
            payments = PaymentService(factory)
            events = BusinessEventService(factory)
            proposals = ProposalService(factory)
            source = ProvenanceInput(
                SourceType.TELEGRAM_USER,
                locator={"chat_id": 1, "message_id": 2},
                description="Synthetic Phase 9",
            )
            product = await products.create_product(
                USER_ID, display_code="KA-45", name="Synthetic Pump", provenance=source
            )
            await products.add_alias(USER_ID, product.id, "Main Pump", provenance=source)
            customer = await customers.create_customer(
                USER_ID, display_name="ABC MMC", provenance=source
            )
            await customers.add_alias(USER_ID, customer.id, "ABC Company", provenance=source)
            fallback_customer = await customers.create_customer(
                USER_ID, display_name="Fallback MMC", provenance=source
            )
            start = datetime(2026, 1, 1, tzinfo=UTC)
            await pricing.add_price(
                USER_ID,
                product_id=product.id,
                amount=Decimal("100.1250"),
                currency="AZN",
                effective_at=start,
                provenance=source,
            )
            await pricing.add_price(
                USER_ID,
                product_id=product.id,
                customer_id=customer.id,
                amount=Decimal("90.5000"),
                currency="AZN",
                effective_at=start,
                provenance=source,
            )
            conflict_at = start + timedelta(days=30)
            for amount in ("110", "111"):
                await pricing.add_price(
                    USER_ID,
                    product_id=product.id,
                    amount=Decimal(amount),
                    currency="AZN",
                    effective_at=conflict_at,
                    provenance=source,
                )
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
                amount=Decimal("10"),
                currency="USD",
                paid_at=start + timedelta(days=1),
                provenance=source,
            )
            for index, kind in enumerate((BusinessEventType.MEETING, BusinessEventType.PHONE_CALL)):
                await events.create_event(
                    USER_ID,
                    customer_id=customer.id,
                    event_type=kind,
                    occurred_at=start + timedelta(days=index),
                    summary=f"Event {index}",
                    provenance=source,
                )
            async with factory.begin() as session:
                file = FileObject(
                    storage_key="phase9/proposal.pdf",
                    original_filename="proposal.pdf",
                    content_type="application/pdf",
                    byte_size=1,
                    sha256="9" * 64,
                    state=FileObjectState.ACTIVE,
                )
                session.add(file)
                await session.flush()
                file_id = file.id
            proposal_a = await proposals.create_proposal(
                USER_ID,
                customer_id=customer.id,
                proposal_date=date(2026, 2, 1),
                status=ProposalStatus.SENT,
                proposal_number="P-A",
                provenance=source,
                documents=[(file_id, ProposalDocumentRelation.PRIMARY)],
                items=[
                    ProposalItemInput(
                        position=1,
                        product_id=product.id,
                        quantity=Decimal("2"),
                        unit_price=Decimal("100"),
                        currency="AZN",
                        line_total=Decimal("200"),
                        provenance=source,
                    ),
                    ProposalItemInput(
                        position=2,
                        raw_product_name="Only A",
                        provenance=source,
                    ),
                ],
            )
            proposal_b = await proposals.create_proposal(
                USER_ID,
                customer_id=customer.id,
                proposal_date=date(2026, 3, 1),
                status=ProposalStatus.ACCEPTED,
                proposal_number="P-B",
                provenance=source,
                items=[
                    ProposalItemInput(
                        position=1,
                        product_id=product.id,
                        quantity=Decimal("3"),
                        unit_price=Decimal("125"),
                        currency="AZN",
                        line_total=Decimal("375"),
                        provenance=source,
                    ),
                    ProposalItemInput(
                        position=2,
                        raw_product_name="Only B",
                        provenance=source,
                    ),
                ],
            )
            registry = BusinessToolRegistry(BusinessQueryService(factory))
            assert len(registry.names) == 13
            assert len(registry.schemas()) == 13

            found = _body(
                await registry.execute("find_product", user_context, {"code_or_alias": "ka 45"})
            )
            assert found["status"] == "OK"
            assert found["data"]["product"]["matched_by"] == "CANONICAL_CODE"  # type: ignore[index]
            assert (
                _body(
                    await registry.execute(
                        "get_product", user_context, {"product_id": str(product.id)}
                    )
                )["status"]
                == "OK"
            )
            alias = _body(
                await registry.execute("find_product", user_context, {"code_or_alias": "MAIN PUMP"})
            )
            assert alias["data"]["product"]["matched_by"] == "ALIAS"  # type: ignore[index]
            assert (
                _body(
                    await registry.execute(
                        "find_customer", user_context, {"name_or_alias": "abc company"}
                    )
                )["status"]
                == "OK"
            )
            assert (
                _body(
                    await registry.execute(
                        "get_customer", user_context, {"customer_id": str(customer.id)}
                    )
                )["status"]
                == "OK"
            )
            assert (
                _body(
                    await registry.execute(
                        "find_customer", user_context, {"name_or_alias": "missing"}
                    )
                )["status"]
                == "NOT_FOUND"
            )

            specific = _body(
                await registry.execute(
                    "resolve_product_price",
                    user_context,
                    {"product": "KA45", "customer": "ABC MMC", "as_of": start.isoformat()},
                )
            )
            assert specific["data"]["price"]["amount"] == "90.5000"  # type: ignore[index]
            no_fallback = _body(
                await registry.execute(
                    "resolve_product_price",
                    user_context,
                    {
                        "product": "KA45",
                        "customer": str(fallback_customer.id),
                        "as_of": start.isoformat(),
                    },
                )
            )
            assert no_fallback["status"] == "NOT_FOUND"
            fallback = _body(
                await registry.execute(
                    "resolve_product_price",
                    user_context,
                    {
                        "product": "KA45",
                        "customer": str(fallback_customer.id),
                        "as_of": start.isoformat(),
                        "allow_general_fallback": True,
                    },
                )
            )
            assert fallback["data"]["price"]["used_general_fallback"] is True  # type: ignore[index]
            conflict = _body(
                await registry.execute(
                    "resolve_product_price",
                    user_context,
                    {"product": "KA45", "as_of": conflict_at.isoformat()},
                )
            )
            assert conflict["status"] == "CONFLICT"
            assert len(conflict["data"]["conflicting_prices"]) == 2  # type: ignore[arg-type,index]
            history = _body(
                await registry.execute(
                    "get_product_price_history",
                    user_context,
                    {"product": "KA45", "limit": 2},
                )
            )
            assert history["data"]["has_more"] is True  # type: ignore[index]

            payment_page = _body(
                await registry.execute(
                    "get_customer_payments",
                    user_context,
                    {"customer": "ABC MMC", "currency": "AZN"},
                )
            )
            assert len(payment_page["data"]["items"]) == 1  # type: ignore[arg-type,index]
            totals = _body(
                await registry.execute(
                    "get_customer_payment_totals", user_context, {"customer": "ABC MMC"}
                )
            )
            assert totals["data"]["totals"] == {"AZN": "25.1250", "USD": "10.0000"}  # type: ignore[index]
            event_page = _body(
                await registry.execute(
                    "get_customer_events",
                    user_context,
                    {"customer": "ABC MMC", "limit": 1},
                )
            )
            assert event_page["data"]["has_more"] is True  # type: ignore[index]
            assert event_page["data"]["items"][0]["event_type"] == "PHONE_CALL"  # type: ignore[index]

            listed = _body(
                await registry.execute(
                    "get_customer_proposals", user_context, {"customer": "ABC MMC"}
                )
            )
            assert listed["data"]["items"][0]["proposal_number"] == "P-B"  # type: ignore[index]
            latest = _body(
                await registry.execute(
                    "get_latest_customer_proposal", user_context, {"customer": "ABC MMC"}
                )
            )
            assert latest["data"]["proposal_id"] == str(proposal_b.id)  # type: ignore[index]
            details = _body(
                await registry.execute(
                    "get_proposal_details", user_context, {"proposal_id": str(proposal_a.id)}
                )
            )
            assert details["data"]["documents"][0]["filename"] == "proposal.pdf"  # type: ignore[index]
            assert details["data"]["items"][1]["raw_product_name"] == "Only A"  # type: ignore[index]
            comparison = _body(
                await registry.execute(
                    "compare_proposals",
                    user_context,
                    {"proposal_a": str(proposal_a.id), "proposal_b": str(proposal_b.id)},
                )
            )
            matched = comparison["data"]["matched_items"][0]  # type: ignore[index]
            assert matched["quantity_delta"] == "1.0000"  # type: ignore[index]
            assert matched["unit_price_delta"] == "25.0000"  # type: ignore[index]

            invalid = _body(
                await registry.execute(
                    "get_customer_payments",
                    user_context,
                    {"customer": "ABC", "limit": 101},
                )
            )
            assert invalid["status"] == "INVALID_REQUEST"
            assert (
                _body(await registry.execute("raw_sql", user_context, {"sql": "SELECT 1"}))[
                    "status"
                ]
                == "INVALID_REQUEST"
            )
            for telegram_id, status in (
                (PENDING_ID, UserStatus.PENDING),
                (DISABLED_ID, UserStatus.DISABLED),
                (REJECTED_ID, UserStatus.REJECTED),
            ):
                context = ToolExecutionContext(
                    user_id=actor_ids[telegram_id], role="USER", request_id=str(status)
                )
                with pytest.raises(AuthorizationDenied):
                    await registry.execute("find_product", context, {"code_or_alias": "KA45"})
            assert (
                _body(
                    await registry.execute("find_product", admin_context, {"code_or_alias": "KA45"})
                )["status"]
                == "OK"
            )
            async with factory.begin() as session:
                session.add_all(
                    [
                        Product(
                            canonical_code=normalize_product_code("Main Pump"),
                            display_code="MAINPUMP",
                            name="Defensive ambiguity fixture",
                            is_active=True,
                            created_by_user_id=actor_ids[USER_ID],
                            source_reference_id=product.source_reference_id,
                        ),
                        Customer(
                            display_name="ABC Company canonical",
                            normalized_name=normalize_customer_name("ABC Company"),
                            is_active=True,
                            created_by_user_id=actor_ids[USER_ID],
                            source_reference_id=customer.source_reference_id,
                        ),
                    ]
                )
            ambiguous_product = _body(
                await registry.execute("find_product", user_context, {"code_or_alias": "Main Pump"})
            )
            assert ambiguous_product["status"] == "AMBIGUOUS"
            assert len(ambiguous_product["data"]["candidates"]) == 2  # type: ignore[arg-type,index]
            ambiguous_customer = _body(
                await registry.execute(
                    "find_customer", user_context, {"name_or_alias": "ABC Company"}
                )
            )
            assert ambiguous_customer["status"] == "AMBIGUOUS"
            assert len(ambiguous_customer["data"]["candidates"]) == 2  # type: ignore[arg-type,index]
            await products.deactivate_product(ADMIN_ID, product.id)
            assert (
                _body(
                    await registry.execute("find_product", user_context, {"code_or_alias": "KA45"})
                )["status"]
                == "NOT_FOUND"
            )
            assert (
                _body(
                    await registry.execute(
                        "find_product",
                        admin_context,
                        {"code_or_alias": "KA45", "include_inactive": True},
                    )
                )["status"]
                == "OK"
            )
        finally:
            await _cleanup(factory)
            await engine.dispose()

    asyncio.run(scenario())
