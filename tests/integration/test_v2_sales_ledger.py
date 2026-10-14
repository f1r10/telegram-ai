"""Sales ledger contract over a disposable migrated PostgreSQL database."""

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.sales_ledger import (
    SaleAlreadyExists,
    SaleDraft,
    SalesLedgerService,
    SalesRequest,
)
from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import AuditEvent, SalesEntry, User, UserRole, UserStatus
from office_assistant.infrastructure.database.session import create_session_factory

pytestmark = pytest.mark.integration
_ACTOR = 992344556677


def test_real_postgres_sales_totals_and_audit() -> None:
    settings = load_settings()
    if not settings.postgres_host or not settings.postgres_password:
        pytest.skip("PostgreSQL integration settings are not configured")

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        actor_id = None
        try:
            async with factory.begin() as session:
                existing = await session.scalar(
                    select(User).where(User.telegram_user_id == _ACTOR)
                )
                if existing is not None:
                    actor_id = existing.id
                else:
                    actor = User(
                        telegram_user_id=_ACTOR,
                        role=UserRole.ADMIN,
                        status=UserStatus.ACTIVE,
                    )
                    session.add(actor)
                    await session.flush()
                    actor_id = actor.id
            async with factory() as session:
                actor = await session.scalar(select(User).where(User.id == actor_id))
                assert actor is not None
            ledger = SalesLedgerService(factory)
            draft = lambda number, amount, currency="AZN": SaleDraft(
                "QA Sales Customer", date(2026, 2, 10), number, Decimal(amount), currency
            )
            await ledger.record(actor, draft("QA-2026-I001", "125.50"))
            await ledger.record(actor, draft("QA-2026-I002", "74.50"))
            await ledger.record(actor, draft("QA-2026-I003", "10.00", "USD"))
            with pytest.raises(SaleAlreadyExists):
                await ledger.record(actor, draft("QA-2026-I001", "999.00"))
            result = await ledger.totals(actor, SalesRequest("qa sales customer", 2026))
            assert [(x.currency, x.invoice_count, x.amount) for x in result] == [
                ("AZN", 2, Decimal("200.00")),
                ("USD", 1, Decimal("10.00")),
            ]
            assert await ledger.totals(actor, SalesRequest("QA Sales Customer", 2025)) == ()
            actor.telegram_user_id = -1
            with pytest.raises(AuthorizationDenied):
                await ledger.totals(actor, SalesRequest("QA Sales Customer", 2026))
            async with factory() as session:
                total_audit = await session.scalar(
                    select(func.count(AuditEvent.id)).where(
                        AuditEvent.actor_user_id == actor_id,
                        AuditEvent.action == "SALES_ENTRY_POSTED",
                    )
                )
                assert total_audit == 3
        finally:
            if actor_id is not None:
                async with factory.begin() as session:
                    await session.execute(
                        delete(AuditEvent).where(
                            AuditEvent.actor_user_id == actor_id,
                            AuditEvent.action == "SALES_ENTRY_POSTED",
                        )
                    )
                    await session.execute(
                        delete(SalesEntry).where(SalesEntry.created_by_user_id == actor_id)
                    )
                    await session.execute(delete(User).where(User.id == actor_id))
            await engine.dispose()

    asyncio.run(scenario())
