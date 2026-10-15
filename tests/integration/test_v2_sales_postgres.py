"""Real PostgreSQL test of money, permissions, uniqueness and audit."""

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, update

from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.sales_ledger import (
    SaleAlreadyExists,
    SaleDraft,
    SalesLedgerService,
    SalesRequest,
    SalesTotal,
)
from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    SalesEntry,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory

pytestmark = pytest.mark.integration
ADMIN_TG = 99996011
STAFF_TG = 99996012


def test_real_postgres_confirmed_sales_annual_totals_and_access() -> None:
    settings = load_settings()
    if not settings.postgres_host or settings.postgres_password is None:
        pytest.skip("Postgres integration environment is not configured")

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        ledger = SalesLedgerService(factory)

        async def cleanup() -> None:
            async with factory.begin() as session:
                actor_ids = select(User.id).where(User.telegram_user_id.in_([ADMIN_TG, STAFF_TG]))
                await session.execute(
                    delete(SalesEntry).where(SalesEntry.created_by_user_id.in_(actor_ids))
                )
                await session.execute(
                    delete(AuditEvent).where(AuditEvent.actor_user_id.in_(actor_ids))
                )
                await session.execute(delete(User).where(User.id.in_(actor_ids)))

        try:
            await cleanup()
            async with factory.begin() as session:
                admin = User(
                    telegram_user_id=ADMIN_TG,
                    role=UserRole.ADMIN,
                    status=UserStatus.ACTIVE,
                )
                staff = User(
                    telegram_user_id=STAFF_TG,
                    role=UserRole.USER,
                    status=UserStatus.ACTIVE,
                )
                session.add_all([admin, staff])
                await session.flush()
                admin_id = admin.id
                staff_id = staff.id
            actor = User(
                id=admin_id,
                telegram_user_id=ADMIN_TG,
                role=UserRole.ADMIN,
                status=UserStatus.ACTIVE,
            )
            employee = User(
                id=staff_id,
                telegram_user_id=STAFF_TG,
                role=UserRole.USER,
                status=UserStatus.ACTIVE,
            )
            first = SaleDraft("A Şirkəti", date(2026, 10, 1), "INV-101", Decimal("10.30"), "AZN")
            second = SaleDraft("a şirkəti", date(2026, 10, 2), "INV-102", Decimal("25.70"), "AZN")
            other = SaleDraft("A Şirkəti", date(2026, 10, 2), "INV-103", Decimal("8.25"), "USD")
            prev = SaleDraft("A Şirkəti", date(2025, 12, 31), "INV-100", Decimal("50.00"), "AZN")
            for draft in (first, second, other, prev):
                await ledger.record(actor, draft)
            with pytest.raises(SaleAlreadyExists):
                await ledger.record(actor, first)
            with pytest.raises(AuthorizationDenied):
                await ledger.record(
                    employee,
                    SaleDraft(
                        "A Şirkəti", date(2026, 10, 2), "UNAUTHORIZED", Decimal("999"), "AZN"
                    ),
                )
            assert await ledger.totals(employee, SalesRequest("A Şirkəti", 2026)) == (
                # Currency ordering is deterministic.
                # Decimal values must never be coerced to float.
                SalesTotal("AZN", 2, Decimal("36.00")),
                SalesTotal("USD", 1, Decimal("8.25")),
            )
            assert await ledger.totals(employee, SalesRequest("A Şirkəti", 2025)) == (
                SalesTotal("AZN", 1, Decimal("50.00")),
            )
            assert await ledger.totals(employee, SalesRequest("B Şirkəti", 2026)) == ()
            async with factory.begin() as session:
                await session.execute(
                    update(SalesEntry)
                    .where(SalesEntry.invoice_number == "INV-102")
                    .values(status="VOIDED")
                )
            after_void = await ledger.totals(employee, SalesRequest("A Şirkəti", 2026))
            assert after_void[0].amount == Decimal("10.30")
            assert after_void[0].invoice_count == 1
            async with factory() as session:
                events = (
                    (
                        await session.execute(
                            select(AuditEvent).where(
                                AuditEvent.actor_user_id == admin_id,
                                AuditEvent.action == "SALES_ENTRY_POSTED",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(events) == 4
            # A reviewed Excel batch must post atomically. An existing invoice
            # anywhere in the batch must roll back all newly proposed records.
            batch = (
                SaleDraft("B Şirkəti", date(2026, 10, 1), "IMP-01", Decimal("20"), "AZN"),
                SaleDraft("B Şirkəti", date(2026, 10, 2), "IMP-02", Decimal("30"), "AZN"),
            )
            written = await ledger.record_many(actor, batch)
            assert len(written) == 2
            expected = (SalesTotal("AZN", 2, Decimal("50.00")),)
            assert await ledger.totals(employee, SalesRequest("B Şirkəti", 2026)) == expected
            with pytest.raises(SaleAlreadyExists):
                await ledger.record_many(
                    actor,
                    (
                        SaleDraft("B Şirkəti", date(2026, 10, 3), "IMP-03", Decimal("99"), "AZN"),
                        batch[0],
                    ),
                )
            assert await ledger.totals(employee, SalesRequest("B Şirkəti", 2026)) == expected
            with pytest.raises(AuthorizationDenied):
                await ledger.record_many(
                    employee,
                    (SaleDraft("B Şirkəti", date(2026, 10, 4), "IMP-04", Decimal("5"), "AZN"),),
                )
            async with factory.begin() as session:
                await session.execute(
                    update(User).where(User.id == staff_id).values(status=UserStatus.DISABLED)
                )
            with pytest.raises(AuthorizationDenied):
                await ledger.totals(employee, SalesRequest("A Şirkəti", 2026))
        finally:
            await cleanup()
            await engine.dispose()

    asyncio.run(scenario())
