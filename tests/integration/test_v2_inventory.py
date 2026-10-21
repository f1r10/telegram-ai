"""Real PostgreSQL stock accounting: consent, concurrency, absence and audit safety."""

import asyncio
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import delete, select

from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.inventory import (
    InventoryConflict,
    InventoryLedgerService,
    InventoryValidationError,
    StockAdjustmentDraft,
    StockDraft,
    StockReservationDraft,
    StockTransferDraft,
)
from office_assistant.application.inventory_xlsx import commit_stock_xlsx, preview_stock_xlsx
from office_assistant.application.products import ProductService
from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
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
            assert state.total is None
            assert state.total_reason == "UNVERIFIED_UNIT"
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
            # The same approved transfer document cannot fund an unrelated
            # warehouse pair; uniqueness is product-wide, not per location.
            await ledger.record(admin, _draft(CODE, "Üçüncü", "OPENING", "0", "O-004"))
            await ledger.record(admin, _draft(CODE, "Dördüncü", "OPENING", "0", "O-005"))
            with pytest.raises(InventoryConflict, match="artıq"):
                await ledger.transfer(
                    admin,
                    StockTransferDraft(CODE, "Üçüncü", "Dördüncü", Decimal("1"), "MOVE-001"),
                )
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
            assert after_transfer.total is None
            assert after_transfer.total_reason == "UNVERIFIED_UNIT"
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

            # Returns, damaged stock and signed corrections use append-only
            # ledger rows with explicit admin approval and a required reason.
            adjustment = StockAdjustmentDraft(
                CODE,
                "Əsas",
                "RETURN",
                Decimal("2.000"),
                "ADJ-001",
                "Returned by verified customer",
            )
            with pytest.raises(AuthorizationDenied):
                await ledger.adjust(employee, adjustment)
            returned = await ledger.adjust(admin, adjustment)
            assert returned.applied_delta == Decimal("2.000")
            assert returned.balance_after == returned.balance_before + Decimal("2.000")
            damaged = await ledger.adjust(
                admin,
                StockAdjustmentDraft(
                    CODE,
                    "Əsas",
                    "DAMAGE",
                    Decimal("1.000"),
                    "ADJ-002",
                    "Damaged and inspected in warehouse",
                ),
            )
            assert damaged.applied_delta == Decimal("-1.000")
            correction = await ledger.adjust(
                admin,
                StockAdjustmentDraft(
                    CODE,
                    "Əsas",
                    "CORRECTION",
                    Decimal("-0.500"),
                    "ADJ-003",
                    "Verified physical stock count discrepancy",
                ),
            )
            assert correction.balance_after == damaged.balance_after - Decimal("0.500")
            with pytest.raises(InventoryConflict, match="artıq"):
                await ledger.adjust(
                    admin,
                    StockAdjustmentDraft(
                        CODE,
                        "Kənar",
                        "RETURN",
                        Decimal("1"),
                        "ADJ-001",
                        "The same document must not post twice",
                    ),
                )
            with pytest.raises(InventoryConflict, match="kifayət"):
                await ledger.adjust(
                    admin,
                    StockAdjustmentDraft(
                        CODE,
                        "Əsas",
                        "WRITE_OFF",
                        Decimal("200"),
                        "ADJ-004",
                        "Broken stock has been reviewed",
                    ),
                )
            with pytest.raises(InventoryConflict, match="açılış"):
                await ledger.adjust(
                    admin,
                    StockAdjustmentDraft(
                        CODE,
                        "Naməlum",
                        "RETURN",
                        Decimal("1"),
                        "ADJ-005",
                        "Verified previous sale returned",
                    ),
                )
            with pytest.raises(InventoryValidationError):
                await ledger.adjust(
                    admin,
                    StockAdjustmentDraft(
                        CODE,
                        "Əsas",
                        "RETURN",
                        Decimal("-2"),
                        "ADJ-006",
                        "Negative return must be rejected",
                    ),
                )
            async with factory() as session:
                adjustments = list(
                    (
                        await session.scalars(
                            select(InventoryMovement).where(
                                InventoryMovement.product_id == product_id,
                                InventoryMovement.reference_key.like("adj:%"),
                            )
                        )
                    ).all()
                )
                assert len(adjustments) == 3
                assert sorted(x.kind for x in adjustments) == ["ISSUE", "ISSUE", "RECEIPT"]
                audit = list(
                    (
                        await session.scalars(
                            select(AuditEvent).where(
                                AuditEvent.actor_user_id == admin_id,
                                AuditEvent.action == "INVENTORY_ADJUSTMENT_POSTED",
                            )
                        )
                    ).all()
                )
                assert len(audit) == 3

            reserve_draft = StockReservationDraft(CODE, "Əsas", Decimal("2.000"), "RES-001")
            with pytest.raises(AuthorizationDenied):
                await ledger.reserve(employee, reserve_draft)
            booked = await ledger.reserve(admin, reserve_draft)
            assert booked.status == "ACTIVE"
            assert booked.on_hand == Decimal("7.250")
            assert booked.reserved == Decimal("2.000")
            assert booked.available == Decimal("5.250")
            with pytest.raises(AuthorizationDenied):
                await ledger.reservation_preview(employee, product_code=CODE, reference="RES-001")
            preview = await ledger.reservation_preview(
                admin, product_code=CODE, reference="RES-001"
            )
            assert preview.status == "ACTIVE"
            assert preview.quantity == booked.quantity
            assert preview.on_hand == booked.on_hand
            assert preview.reserved == booked.reserved
            assert preview.available == booked.available
            with pytest.raises(InventoryConflict, match="Aktiv"):
                await ledger.reservation_preview(
                    admin, product_code=CODE, reference="UNKNOWN-RESERVATION"
                )
            with pytest.raises(InventoryConflict, match="artıq"):
                await ledger.reserve(admin, reserve_draft)
            with pytest.raises(InventoryConflict, match="sərbəst"):
                await ledger.reserve(
                    admin, StockReservationDraft(CODE, "Əsas", Decimal("6"), "RES-002")
                )
            with pytest.raises(InventoryConflict, match="Rezerv"):
                await ledger.record(admin, _draft(CODE, "Əsas", "ISSUE", "6", "I-RES-1"))
            with pytest.raises(InventoryConflict, match="rezerv"):
                await ledger.transfer(
                    admin,
                    StockTransferDraft(CODE, "Əsas", "Kənar", Decimal("6"), "MOVE-RES-1"),
                )
            with pytest.raises(InventoryConflict, match="rezerv"):
                await ledger.adjust(
                    admin,
                    StockAdjustmentDraft(
                        CODE,
                        "Əsas",
                        "WRITE_OFF",
                        Decimal("6"),
                        "ADJ-RES-1",
                        "Confirmed scrapped item physically present",
                    ),
                )
            extra = await ledger.reserve(
                admin, StockReservationDraft(CODE, "Əsas", Decimal("1"), "RES-003")
            )
            assert extra.reserved == Decimal("3.000")
            cancelled = await ledger.reservation_action(
                admin, product_code=CODE, reference="RES-003", action="CANCEL"
            )
            assert cancelled.status == "CANCELLED"
            assert cancelled.reserved == Decimal("2.000")
            with pytest.raises(InventoryConflict, match="Aktiv"):
                await ledger.reservation_preview(admin, product_code=CODE, reference="RES-003")
            again = await ledger.reservation_action(
                admin, product_code=CODE, reference="RES-003", action="CANCEL"
            )
            assert again == cancelled
            consumed = await ledger.reservation_action(
                admin, product_code=CODE, reference="RES-001", action="CONSUME"
            )
            assert consumed.status == "CONSUMED"
            assert consumed.on_hand == Decimal("5.250")
            assert consumed.reserved == Decimal("0")
            assert consumed.available == Decimal("5.250")
            assert (
                await ledger.reservation_action(
                    admin, product_code=CODE, reference="RES-001", action="CONSUME"
                )
                == consumed
            )
            with pytest.raises(InventoryConflict, match="başqa"):
                await ledger.reservation_action(
                    admin, product_code=CODE, reference="RES-001", action="CANCEL"
                )
            concurrent = await asyncio.gather(
                ledger.reserve(admin, StockReservationDraft(CODE, "Əsas", Decimal("4"), "RES-004")),
                ledger.reserve(admin, StockReservationDraft(CODE, "Əsas", Decimal("4"), "RES-005")),
                return_exceptions=True,
            )
            assert sum(isinstance(row, InventoryConflict) for row in concurrent) == 1
            assert sum(not isinstance(row, Exception) for row in concurrent) == 1
            async with factory() as session:
                reserved_rows = list(
                    (
                        await session.scalars(
                            select(InventoryReservation).where(
                                InventoryReservation.product_id == product_id
                            )
                        )
                    ).all()
                )
                assert len(reserved_rows) == 3
                assert {row.status for row in reserved_rows} == {"CONSUMED", "CANCELLED", "ACTIVE"}
                consumed_movements = list(
                    (
                        await session.scalars(
                            select(InventoryMovement).where(
                                InventoryMovement.product_id == product_id,
                                InventoryMovement.reference_key == "res:res-001",
                            )
                        )
                    ).all()
                )
                assert len(consumed_movements) == 1
                assert consumed_movements[0].kind == "ISSUE"

            # One invalid row aborts every stock movement and audit entry in
            # the workbook, even when a previous row was otherwise valid.
            state_before = await ledger.snapshot(admin, CODE)

            def make_stock_workbook(second_quantity: int) -> bytes:
                workbook = Workbook()
                page = workbook.active
                assert page is not None
                page.append(["product_code", "warehouse", "kind", "quantity", "reference"])
                page.append([CODE, "Əsas", "GİRİŞ", 2, "BULK-IN-1"])
                page.append([CODE, "Əsas", "ÇIXIŞ", second_quantity, "BULK-OUT-1"])
                buffer = BytesIO()
                workbook.save(buffer)
                workbook.close()
                return buffer.getvalue()

            invalid_body = make_stock_workbook(200)
            invalid_preview = preview_stock_xlsx(invalid_body)
            with pytest.raises(InventoryConflict):
                await commit_stock_xlsx(
                    ledger, admin, invalid_body, confirmed_sha256=invalid_preview.sha256
                )
            assert await ledger.snapshot(admin, CODE) == state_before
            async with factory() as session:
                uncommitted = await session.scalar(
                    select(InventoryMovement.id).where(
                        InventoryMovement.product_id == product_id,
                        InventoryMovement.reference_key == "bulk-in-1",
                    )
                )
                assert uncommitted is None

            valid_body = make_stock_workbook(2)
            valid_preview = preview_stock_xlsx(valid_body)
            with pytest.raises(AuthorizationDenied):
                await commit_stock_xlsx(
                    ledger, employee, valid_body, confirmed_sha256=valid_preview.sha256
                )
            with pytest.raises(InventoryValidationError, match="dəyişib"):
                await commit_stock_xlsx(ledger, admin, valid_body, confirmed_sha256="0" * 64)
            posted = await commit_stock_xlsx(
                ledger, admin, valid_body, confirmed_sha256=valid_preview.sha256
            )
            assert len(posted) == 2
            assert posted[-1].balance_after == next(
                loc.quantity for loc in state_before.locations if loc.warehouse == "Əsas"
            )
            assert await ledger.snapshot(admin, CODE) == state_before
            with pytest.raises(InventoryConflict):
                await commit_stock_xlsx(
                    ledger, admin, valid_body, confirmed_sha256=valid_preview.sha256
                )

            history = await ledger.history(employee, CODE, limit=30)
            assert len(history) == 17
            assert history[0].product_code == CODE
            assert all(item.document_reference for item in history)
            main_history = await ledger.history(employee, CODE, warehouse=" ƏSAS ", limit=25)
            assert main_history
            assert all(item.warehouse == "Əsas" for item in main_history)
            assert len(await ledger.history(employee, CODE, limit=1)) == 1
            with pytest.raises(InventoryValidationError):
                await ledger.history(employee, CODE, limit=0)
            with pytest.raises(InventoryValidationError):
                await ledger.history(employee, CODE, warehouse="  ")

            async with factory.begin() as session:
                db_actor = await session.get(User, employee_id)
                assert db_actor is not None
                db_actor.status = UserStatus.DISABLED
            with pytest.raises(AuthorizationDenied):
                await ledger.snapshot(employee, CODE)
            with pytest.raises(AuthorizationDenied):
                await ledger.history(employee, CODE)

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
                        delete(InventoryReservation).where(
                            InventoryReservation.product_id == product_id
                        )
                    )
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
