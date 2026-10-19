"""Real PostgreSQL stock accounting: consent, concurrency, absence and audit safety."""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.inventory import (
    InventoryConflict,
    InventoryLedgerService,
    InventoryValidationError,
    StockDraft,
    StockTransferDraft,
)
from office_assistant.application.products import ProductService
from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    InventoryBalance,
    InventoryMovement,
    Product,
    SourceReference,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory

pytestmark = pytest.mark.integration

ADMIN = 9382801
EMPLOYEE = 9382802
CODE = "ZZSTOCKINTEG26"


def test_stock_opening_movements_retry_and_access_on_disposable_postgres() -> None:
    settings = load_settings()
    if not settings.postgres_host or not settings.postgres_password:
        pytest.skip("Disposable PostgreSQL integration configuration missing")

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        admin_id = employee_id = None
        product_id = source_id = None
        try:
            async with factory.begin() as session:
                admin = User(telegram_user_id=ADMIN, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
                employee = User(
                    telegram_user_id=EMPLOYEE, role=UserRole.USER, status=UserStatus.ACTIVE
                )
                session.add_all([admin, employee])
                await session.flush()
                admin_id, employee_id = admin.id, employee.id
            product = await ProductService(factory).create_product(
                ADMIN, display_code=CODE, name="Test inventory part"
            )
            product_id = product.id
            source_id = product.source_reference_id
            ledger = InventoryLedgerService(factory)
            unknown = await ledger.snapshot(admin, CODE)
            assert unknown.locations == ()
            assert unknown.total is None
            with pytest.raises(AuthorizationDenied):
                await ledger.record(employee, _draft(CODE, "Əsas", "OPENING", "1", "A-001"))
            with pytest.raises(InventoryConflict, match="naməlumdur"):
                await ledger.record(admin, _draft(CODE, "Əsas", "RECEIPT", "2", "R-001"))

            opening = await ledger.record(admin, _draft(CODE, "Əsas", "OPENING", "0", "O-001"))
            assert opening.balance_after == Decimal("0")
            with pytest.raises(InventoryConflict, match="artıq"):
                await ledger.record(admin, _draft(CODE, "Əsas", "OPENING", "10", "O-002"))
            received = await ledger.record(admin, _draft(CODE, "Əsas", "RECEIPT", "9.750", "R-001"))
            assert received.balance_after == Decimal("9.750")
            with pytest.raises(InventoryConflict):
                await ledger.record(admin, _draft(CODE, "Əsas", "RECEIPT", "9.750", "R-001"))
            with pytest.raises(InventoryConflict, match="kifayət"):
                await ledger.record(admin, _draft(CODE, "Əsas", "ISSUE", "10", "I-010"))

            two = await asyncio.gather(
                ledger.record(admin, _draft(CODE, "Əsas", "ISSUE", "6", "I-001")),
                ledger.record(admin, _draft(CODE, "Əsas", "ISSUE", "6", "I-002")),
                return_exceptions=True,
            )
            assert sum(isinstance(x, InventoryConflict) for x in two) == 1
            assert sum(not isinstance(x, Exception) for x in two) == 1
            await ledger.record(admin, _draft(CODE, "Kənar", "OPENING", "2.500", "O-003"))
            await ledger.record(admin, _draft(CODE, "Kənar", "RECEIPT", "1.000", "R-002"))
            state = await ledger.snapshot(employee, CODE)
            assert state.total == Decimal("7.250")
            assert len(state.locations) == 2

            async with factory() as session:
                balance_rows = list((await session.scalars(select(InventoryBalance))).all())
                movement_rows = list(
                    (
                        await session.scalars(
                            select(InventoryMovement).where(
                                InventoryMovement.product_id == product_id
                            )
                        )
                    ).all()
                )
                assert len([r for r in balance_rows if r.product_id == product_id]) == 2
                assert len(movement_rows) == 5
                actions = list(
                    (
                        await session.scalars(
                            select(AuditEvent).where(
                                AuditEvent.actor_user_id == admin_id,
                                AuditEvent.action == "INVENTORY_MOVEMENT_POSTED",
                            )
                        )
                    ).all()
                )
                assert len(actions) == 5

            transfer = StockTransferDraft(CODE, "Kənar", "Əsas", Decimal("1.000"), "MOVE-001")
            with pytest.raises(AuthorizationDenied):
                await ledger.transfer(employee, transfer)
            done = await ledger.transfer(admin, transfer)
            assert done.source_balance_after == Decimal("2.500")
            assert done.destination_balance_after == Decimal("4.750")
            with pytest.raises(InventoryConflict, match="artıq"):
                await ledger.transfer(admin, transfer)
            with pytest.raises(InventoryConflict, match="açılış"):
                await ledger.transfer(
                    admin,
                    StockTransferDraft(CODE, "Kənar", "Naməlum", Decimal("1"), "MOVE-002"),
                )
            with pytest.raises(InventoryConflict, match="kifayət"):
                await ledger.transfer(
                    admin,
                    StockTransferDraft(CODE, "Kənar", "Əsas", Decimal("8"), "MOVE-003"),
                )
            competing = await asyncio.gather(
                ledger.transfer(
                    admin, StockTransferDraft(CODE, "Kənar", "Əsas", Decimal("2"), "MOVE-004")
                ),
                ledger.transfer(
                    admin, StockTransferDraft(CODE, "Kənar", "Əsas", Decimal("2"), "MOVE-005")
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(x, InventoryConflict) for x in competing) == 1
            assert sum(not isinstance(x, Exception) for x in competing) == 1
            after_transfer = await ledger.snapshot(admin, CODE)
            assert after_transfer.total == Decimal("7.250")
            async with factory() as session:
                transfers = list(
                    (
                        await session.scalars(
                            select(InventoryMovement).where(
                                InventoryMovement.product_id == product_id,
                                InventoryMovement.reference_key.like("xfer:%"),
                            )
                        )
                    ).all()
                )
                assert len(transfers) == 4
                assert {item.kind for item in transfers} == {"ISSUE", "RECEIPT"}

            async with factory.begin() as session:
                db_actor = await session.get(User, employee_id)
                assert db_actor is not None
                db_actor.status = UserStatus.DISABLED
            with pytest.raises(AuthorizationDenied):
                await ledger.snapshot(employee, CODE)

            async with factory.begin() as session:
                product_db = await session.get(Product, product_id)
                assert product_db is not None
                product_db.is_active = False
            with pytest.raises(InventoryValidationError):
                await ledger.snapshot(admin, CODE)
        finally:
            async with factory.begin() as session:
                if product_id is not None:
                    await session.execute(
                        delete(InventoryMovement).where(InventoryMovement.product_id == product_id)
                    )
                    await session.execute(
                        delete(InventoryBalance).where(InventoryBalance.product_id == product_id)
                    )
                if admin_id is not None:
                    await session.execute(
                        delete(AuditEvent).where(AuditEvent.actor_user_id == admin_id)
                    )
                if product_id is not None:
                    await session.execute(delete(Product).where(Product.id == product_id))
                if source_id is not None:
                    await session.execute(
                        delete(SourceReference).where(SourceReference.id == source_id)
                    )
                await session.execute(
                    delete(User).where(User.telegram_user_id.in_([ADMIN, EMPLOYEE]))
                )
            await engine.dispose()

    asyncio.run(scenario())


def _draft(code: str, warehouse: str, kind: str, amount: str, reference: str) -> StockDraft:
    return StockDraft(code, warehouse, kind, Decimal(amount), reference)
