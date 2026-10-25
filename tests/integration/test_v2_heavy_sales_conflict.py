"""Bounded write-collision test on disposable CI Postgres ONLY.

All data are synthetic. Never run against a staging or production database.
"""

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
ADMIN_ID = 99997771
USER_ID = 99997772
CUSTOMER = "CI Heavy Synthetic Customer"


def test_concurrent_duplicate_invoices_are_atomic_and_permission_gated() -> None:
    settings = load_settings()
    # Hard stop: this test *must never* mutate an actual company or Kali staging DB.
    if settings.postgres_db != "office_assistant_test":
        pytest.skip("Heavy sales concurrency tests only allowed on disposable CI database")

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        ledger = SalesLedgerService(factory)
        actor_ids = select(User.id).where(User.telegram_user_id.in_((ADMIN_ID, USER_ID)))

        async def cleanup() -> None:
            async with factory.begin() as session:
                await session.execute(
                    delete(AuditEvent).where(AuditEvent.actor_user_id.in_(actor_ids))
                )
                await session.execute(
                    delete(SalesEntry).where(SalesEntry.created_by_user_id.in_(actor_ids))
                )
                await session.execute(delete(User).where(User.id.in_(actor_ids)))

        try:
            await cleanup()
            async with factory.begin() as session:
                admin = User(
                    telegram_user_id=ADMIN_ID,
                    role=UserRole.ADMIN,
                    status=UserStatus.ACTIVE,
                )
                staff = User(
                    telegram_user_id=USER_ID,
                    role=UserRole.USER,
                    status=UserStatus.ACTIVE,
                )
                session.add_all((admin, staff))
                await session.flush()
                admin_pk, staff_pk = admin.id, staff.id

            real_admin = User(
                id=admin_pk,
                telegram_user_id=ADMIN_ID,
                role=UserRole.ADMIN,
                status=UserStatus.ACTIVE,
            )
            real_staff = User(
                id=staff_pk,
                telegram_user_id=USER_ID,
                role=UserRole.USER,
                status=UserStatus.ACTIVE,
            )
            for wave in range(4):
                draft = SaleDraft(
                    CUSTOMER,
                    date(2026, 10, 25),
                    f"CI-UAT-COLLISION-{wave}",
                    Decimal("9.99"),
                    "AZN",
                )
                # Eight simultaneous posts of the same invoice: one and only
                # one can commit; no duplicate events or partial invoice rows.
                attempts = await asyncio.wait_for(
                    asyncio.gather(
                        *(ledger.record(real_admin, draft) for _ in range(8)),
                        return_exceptions=True,
                    ),
                    timeout=30,
                )
                assert sum(isinstance(value, SalesEntry) for value in attempts) == 1
                assert sum(isinstance(value, SaleAlreadyExists) for value in attempts) == 7
            denied = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        ledger.record(
                            real_staff,
                            SaleDraft(
                                CUSTOMER,
                                date(2026, 10, 25),
                                f"CI-UNAUTHORIZED-{index}",
                                Decimal("999"),
                                "AZN",
                            ),
                        )
                        for index in range(8)
                    ),
                    return_exceptions=True,
                ),
                timeout=30,
            )
            assert all(isinstance(value, AuthorizationDenied) for value in denied)
            assert await ledger.totals(real_staff, SalesRequest(CUSTOMER, 2026)) == (
                SalesTotal("AZN", 4, Decimal("39.96")),
            )
            async with factory() as session:
                audited = await session.scalar(
                    select(func.count(AuditEvent.id)).where(
                        AuditEvent.actor_user_id == admin_pk,
                        AuditEvent.action == "SALES_ENTRY_POSTED",
                    )
                )
                assert audited == 4
        finally:
            await cleanup()
            await engine.dispose()

    asyncio.run(scenario())
