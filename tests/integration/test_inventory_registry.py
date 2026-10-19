"""Disposable PostgreSQL registry tests: no silent units, races or shared privilege."""

import asyncio
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import delete, select

from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.inventory import (
    InventoryConflict,
    InventoryLedgerService,
    InventoryValidationError,
    StockDraft,
    StockReservationDraft,
    StockTransferDraft,
)
from office_assistant.application.inventory_registry import InventoryRegistryService
from office_assistant.application.products import ProductService
from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    InventoryBalance,
    InventoryMovement,
    InventoryProductUnit,
    InventoryReservation,
    InventoryUnitConversion,
    InventoryWarehouse,
    Product,
    SourceReference,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory

pytestmark = pytest.mark.integration
ADMIN = 9382811
EMPLOYEE = 9382812
CODE = "ZZUNITPOLICY2026"
LEGACY = "ZZLEGACYUNIT2026"


def test_warehouse_and_product_unit_registry_is_explicit_and_admin_approved() -> None:
    settings = load_settings()
    if not settings.postgres_host or not settings.postgres_password:
        pytest.skip("Disposable PostgreSQL integration configuration missing")

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        actor_id = None
        product_ids: list[UUID] = []
        source_ids: list[UUID] = []
        try:
            async with factory.begin() as session:
                admin = User(telegram_user_id=ADMIN, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
                employee = User(
                    telegram_user_id=EMPLOYEE, role=UserRole.USER, status=UserStatus.ACTIVE
                )
                session.add_all([admin, employee])
                await session.flush()
                actor_id = admin.id
            for code in (CODE, LEGACY):
                product = await ProductService(factory).create_product(
                    ADMIN, display_code=code, name="Synthetic unit policy stock"
                )
                product_ids.append(product.id)
                source_ids.append(product.source_reference_id)
            service = InventoryRegistryService(factory)
            with pytest.raises(AuthorizationDenied):
                await service.register_warehouse(employee, "Test Əsas")
            with pytest.raises(InventoryValidationError):
                await service.register_warehouse(admin, "  ")
            warehouse = await service.register_warehouse(admin, "Test Əsas")
            assert warehouse.status == "ACTIVE"
            assert warehouse.key == "test əsas"
            assert (await service.list_warehouses(employee))[0].name == "Test Əsas"
            with pytest.raises(InventoryConflict, match="artıq"):
                await service.register_warehouse(admin, "test ƏSAS")
            with pytest.raises(AuthorizationDenied):
                await service.declare_unit(employee, product_code=CODE, base_unit="PCS")
            unit = await service.declare_unit(admin, product_code=CODE, base_unit="PCS")
            assert unit.base_unit == "PCS"
            with pytest.raises(InventoryConflict, match="artıq"):
                await service.declare_unit(admin, product_code=CODE, base_unit="KG")
            with pytest.raises(InventoryValidationError):
                await service.declare_conversion(
                    admin, product_code=CODE, from_unit="METRE", factor=Decimal(2)
                )
            with pytest.raises(InventoryConflict, match="yoxdur"):
                await service.convert(
                    employee, product_code=CODE, from_unit="BOX", quantity=Decimal(2)
                )
            await service.declare_conversion(
                admin, product_code=CODE, from_unit="BOX", factor=Decimal("12.000")
            )
            assert (
                await service.convert(
                    employee,
                    product_code=CODE,
                    from_unit="BOX",
                    quantity=Decimal("2.5"),
                )
            ) == (Decimal("30.0000"), "PCS")
            assert (
                await service.convert(
                    employee,
                    product_code=CODE,
                    from_unit="PCS",
                    quantity=Decimal("2"),
                )
            ) == (Decimal("2"), "PCS")
            with pytest.raises(InventoryConflict, match="artıq"):
                await service.declare_conversion(
                    admin, product_code=CODE, from_unit="BOX", factor=Decimal(6)
                )
            await service.declare_conversion(
                admin, product_code=CODE, from_unit="KG", factor=Decimal("0.333")
            )
            with pytest.raises(InventoryValidationError, match="dəqiqliyə"):
                await service.convert(
                    employee,
                    product_code=CODE,
                    from_unit="KG",
                    quantity=Decimal("0.001"),
                )
            with pytest.raises(InventoryValidationError):
                await service.convert(
                    employee,
                    product_code=CODE,
                    from_unit="KG",
                    quantity=Decimal("-1"),
                )
            ledger = InventoryLedgerService(factory)
            with pytest.raises(InventoryValidationError, match="vahidi"):
                await ledger.record(
                    admin, StockDraft(CODE, "Test Əsas", "OPENING", Decimal(5), "OPEN-UOM")
                )
            with pytest.raises(InventoryValidationError, match="vahidi"):
                await ledger.record(
                    admin, StockDraft(CODE, "Test Əsas", "OPENING", Decimal(5), "OPEN-UOM", "BOX")
                )
            with pytest.raises(InventoryConflict, match="reyestrdə"):
                await ledger.record(
                    admin, StockDraft(CODE, "Naməlum", "OPENING", Decimal(5), "OPEN-UOM", "PCS")
                )
            opened = await ledger.record(
                admin, StockDraft(CODE, "Test Əsas", "OPENING", Decimal(5), "OPEN-UOM", "PCS")
            )
            assert opened.balance_after == Decimal(5)
            assert (await ledger.snapshot(employee, CODE)).unit_code == "PCS"
            with pytest.raises(InventoryValidationError, match="vahidi"):
                await ledger.record_batch(
                    admin,
                    (StockDraft(CODE, "Test Əsas", "RECEIPT", Decimal(1), "BATCH-UOM"),),
                    workbook_sha256="a" * 64,
                )
            batch = await ledger.record_batch(
                admin,
                (StockDraft(CODE, "Test Əsas", "RECEIPT", Decimal(1), "BATCH-UOM", "PCS"),),
                workbook_sha256="b" * 64,
            )
            assert batch[0].balance_after == Decimal(6)
            await service.register_warehouse(admin, "Test Kənar")
            await ledger.record(
                admin, StockDraft(CODE, "Test Kənar", "OPENING", Decimal(0), "SIDE-UOM", "PCS")
            )
            with pytest.raises(InventoryValidationError, match="vahidi"):
                await ledger.transfer(
                    admin,
                    StockTransferDraft(CODE, "Test Əsas", "Test Kənar", Decimal(1), "TRANSFER-UOM"),
                )
            moved = await ledger.transfer(
                admin,
                StockTransferDraft(
                    CODE, "Test Əsas", "Test Kənar", Decimal(1), "TRANSFER-UOM", "PCS"
                ),
            )
            assert moved.source_balance_after == Decimal(5)
            with pytest.raises(InventoryValidationError, match="vahidi"):
                await ledger.reserve(
                    admin, StockReservationDraft(CODE, "Test Əsas", Decimal(1), "RES-UOM")
                )
            reserved = await ledger.reserve(
                admin, StockReservationDraft(CODE, "Test Əsas", Decimal(1), "RES-UOM", "PCS")
            )
            assert reserved.available == Decimal(4)
            # Unitless historic rows cannot acquire a guessed base unit or
            # cause a warehouse to be silently promoted to a registered location.
            ledger = InventoryLedgerService(factory)
            await ledger.record(
                admin,
                StockDraft(LEGACY, "Köhnə", "OPENING", Decimal("4"), "OLD-001"),
            )
            with pytest.raises(InventoryConflict, match="audit"):
                await service.declare_unit(admin, product_code=LEGACY, base_unit="PCS")
            with pytest.raises(InventoryConflict, match="tarixi"):
                await service.register_warehouse(admin, "Köhnə")
            async with factory() as session:
                assert (await session.scalar(select(InventoryProductUnit.product_id))) is not None
                assert (await session.scalar(select(InventoryUnitConversion.id))) is not None
            async with factory.begin() as session:
                employee_db = await session.scalar(
                    select(User).where(User.telegram_user_id == EMPLOYEE)
                )
                assert employee_db is not None
                employee_db.status = UserStatus.DISABLED
            with pytest.raises(AuthorizationDenied):
                await service.list_warehouses(employee)
            with pytest.raises(AuthorizationDenied):
                await service.convert(
                    employee, product_code=CODE, from_unit="BOX", quantity=Decimal(2)
                )
        finally:
            async with factory.begin() as session:
                for table in (
                    InventoryUnitConversion,
                    InventoryProductUnit,
                    InventoryReservation,
                    InventoryMovement,
                    InventoryBalance,
                ):
                    await session.execute(delete(table).where(table.product_id.in_(product_ids)))
                await session.execute(
                    delete(InventoryWarehouse).where(
                        InventoryWarehouse.location_key.in_(["test əsas", "test kənar"])
                    )
                )
                if actor_id is not None:
                    await session.execute(
                        delete(AuditEvent).where(AuditEvent.actor_user_id == actor_id)
                    )
                await session.execute(delete(Product).where(Product.id.in_(product_ids)))
                await session.execute(
                    delete(SourceReference).where(SourceReference.id.in_(source_ids))
                )
                await session.execute(
                    delete(User).where(User.telegram_user_id.in_([ADMIN, EMPLOYEE]))
                )
            await engine.dispose()

    asyncio.run(scenario())
