"""Real stock domain transactions on a separately created disposable PostgreSQL ONLY.

The companion shell launcher creates an ephemeral PostgreSQL 17 container,
migrates its schema, runs this scenario, then deletes the container. Never
point this script to tgstage_postgres or any operator/company database.
"""

import argparse
import asyncio
import json
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import func, select

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
from office_assistant.application.inventory_registry import InventoryRegistryService
from office_assistant.application.inventory_xlsx import commit_stock_xlsx, preview_stock_xlsx
from office_assistant.application.products import ProductService
from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import AuditEvent, User, UserRole, UserStatus
from office_assistant.infrastructure.database.session import create_session_factory

DB_NAME = "office_assistant_stock_acceptance_test"
HOST_PREFIX = "tgstage_stock_drill_"
PRODUCT_CODE = "QASTOCKACCEPT26"


def safe_target(settings: Settings) -> bool:
    """Refuse real DB even when caller explicitly requests --apply."""

    return (
        settings.app_env == "test"
        and settings.postgres_db == DB_NAME
        and bool(settings.postgres_host)
        and settings.postgres_host.startswith(HOST_PREFIX)
        and settings.telegram_polling_enabled is False
    )


def workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["product_code", "warehouse", "kind", "quantity", "unit", "reference"])
    sheet.append([PRODUCT_CODE, "QA Əsas", "GİRİŞ", 1, "PCS", "QA-XLSX-R-1"])
    sheet.append([PRODUCT_CODE, "QA Əsas", "ÇIXIŞ", 0.5, "PCS", "QA-XLSX-I-1"])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


async def scenario(settings: Settings) -> dict[str, object]:
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    passed: list[str] = []
    try:
        async with factory.begin() as session:
            admin = User(telegram_user_id=939287001, role=UserRole.ADMIN, status=UserStatus.ACTIVE)
            employee = User(
                telegram_user_id=939287002, role=UserRole.USER, status=UserStatus.ACTIVE
            )
            session.add_all([admin, employee])
            await session.flush()
            admin_id = admin.id

        product = await ProductService(factory).create_product(
            939287001, display_code=PRODUCT_CODE, name="Disposable QA synthetic product"
        )
        assert product.canonical_code == PRODUCT_CODE

        registry = InventoryRegistryService(factory)
        ledger = InventoryLedgerService(factory)

        unknown = await ledger.snapshot(employee, PRODUCT_CODE)
        assert unknown.total is None
        passed.append("unknown_is_not_zero")

        try:
            await registry.register_warehouse(employee, "QA Əsas")
        except AuthorizationDenied:
            passed.append("employee_registry_denied")
        else:
            raise AssertionError("Ordinary user registered a warehouse")

        await registry.register_warehouse(admin, "QA Əsas")
        await registry.register_warehouse(admin, "QA Kənar")
        await registry.declare_unit(admin, product_code=PRODUCT_CODE, base_unit="PCS")
        await registry.declare_conversion(
            admin, product_code=PRODUCT_CODE, from_unit="BOX", factor=Decimal("12")
        )
        assert await registry.convert(
            employee, product_code=PRODUCT_CODE, from_unit="BOX", quantity=Decimal("2")
        ) == (Decimal("24.0000"), "PCS")
        passed.append("warehouse_units_and_conversion")

        try:
            await ledger.record(
                employee, StockDraft(PRODUCT_CODE, "QA Əsas", "OPENING", Decimal(1), "NO")
            )
        except AuthorizationDenied:
            passed.append("employee_stock_write_denied")
        else:
            raise AssertionError("Ordinary user posted stock")

        try:
            await ledger.record(
                admin, StockDraft(PRODUCT_CODE, "QA Əsas", "OPENING", Decimal(1), "BAD", "BOX")
            )
        except InventoryValidationError:
            passed.append("wrong_unit_rejected")
        else:
            raise AssertionError("Incorrect base unit posted")

        await ledger.record(
            admin, StockDraft(PRODUCT_CODE, "QA Əsas", "OPENING", Decimal(10), "QA-O-1", "PCS")
        )
        await ledger.record(
            admin, StockDraft(PRODUCT_CODE, "QA Kənar", "OPENING", Decimal(0), "QA-O-2", "PCS")
        )
        await ledger.record(
            admin, StockDraft(PRODUCT_CODE, "QA Əsas", "RECEIPT", Decimal(5), "QA-R-1", "PCS")
        )
        moved = await ledger.transfer(
            admin,
            StockTransferDraft(PRODUCT_CODE, "QA Əsas", "QA Kənar", Decimal(4), "QA-T-1", "PCS"),
        )
        assert moved.source_balance_after == Decimal(11)
        assert moved.destination_balance_after == Decimal(4)
        passed.append("opening_receipt_atomic_transfer")

        try:
            await ledger.transfer(
                admin,
                StockTransferDraft(
                    PRODUCT_CODE, "QA Əsas", "QA Kənar", Decimal(4), "QA-T-1", "PCS"
                ),
            )
        except InventoryConflict:
            passed.append("duplicate_transfer_rejected")
        else:
            raise AssertionError("Repeated transfer posted")

        reserved = await ledger.reserve(
            admin, StockReservationDraft(PRODUCT_CODE, "QA Əsas", Decimal(3), "QA-RES-1", "PCS")
        )
        assert reserved.available == Decimal(8)
        try:
            await ledger.record(
                admin,
                StockDraft(PRODUCT_CODE, "QA Əsas", "ISSUE", Decimal(9), "QA-I-1", "PCS"),
            )
        except InventoryConflict:
            passed.append("reserved_stock_protected")
        else:
            raise AssertionError("Reserved inventory was overspent")

        cancelled = await ledger.reservation_action(
            admin, product_code=PRODUCT_CODE, reference="QA-RES-1", action="CANCEL"
        )
        assert cancelled.status == "CANCELLED"
        assert cancelled.on_hand == Decimal(11)
        await ledger.reserve(
            admin, StockReservationDraft(PRODUCT_CODE, "QA Əsas", Decimal(2), "QA-RES-2", "PCS")
        )
        consumed = await ledger.reservation_action(
            admin, product_code=PRODUCT_CODE, reference="QA-RES-2", action="CONSUME"
        )
        assert consumed.status == "CONSUMED"
        assert consumed.on_hand == Decimal(9)
        passed.append("reservation_cancel_and_consume")

        for kind, amount, reference, expected in (
            ("RETURN", "2", "QA-A-1", "11"),
            ("DAMAGE", "1", "QA-A-2", "10"),
            ("CORRECTION", "-0.5", "QA-A-3", "9.5"),
        ):
            result = await ledger.adjust(
                admin,
                StockAdjustmentDraft(
                    PRODUCT_CODE,
                    "QA Əsas",
                    kind,
                    Decimal(amount),
                    reference,
                    "Synthetic physical-count adjustment",
                    "PCS",
                ),
            )
            assert result.balance_after == Decimal(expected)
        passed.append("return_damage_signed_correction")

        workbook = workbook_bytes()
        preview = preview_stock_xlsx(workbook)
        assert preview.count == 2
        try:
            await commit_stock_xlsx(ledger, admin, workbook, confirmed_sha256="0" * 64)
        except InventoryValidationError:
            passed.append("xlsx_digest_change_rejected")
        else:
            raise AssertionError("Unapproved workbook was imported")
        posted = await commit_stock_xlsx(ledger, admin, workbook, confirmed_sha256=preview.sha256)
        assert len(posted) == 2
        try:
            await commit_stock_xlsx(ledger, admin, workbook, confirmed_sha256=preview.sha256)
        except InventoryConflict:
            passed.append("xlsx_replay_rejected")
        else:
            raise AssertionError("Workbook replay posted")
        passed.append("xlsx_atomic_import")

        attempts = await asyncio.gather(
            ledger.record(
                admin, StockDraft(PRODUCT_CODE, "QA Əsas", "ISSUE", Decimal(8), "QA-C-1", "PCS")
            ),
            ledger.record(
                admin, StockDraft(PRODUCT_CODE, "QA Əsas", "ISSUE", Decimal(8), "QA-C-2", "PCS")
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(item, InventoryConflict) for item in attempts) == 1
        assert sum(not isinstance(item, BaseException) for item in attempts) == 1
        final = await ledger.snapshot(employee, PRODUCT_CODE)
        assert final.unit_code == "PCS"
        assert final.total == Decimal(6)
        passed.append("concurrent_overspend_rejected")

        async with factory() as session:
            audit_count = await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.actor_user_id == admin_id)
            )
        assert audit_count is not None and audit_count >= 10
        passed.append("audit_events_persisted")
        return {
            "status": "ISOLATED_STOCK_UAT_PASS",
            "checks": passed,
            "final_synthetic_stock": "6.000 PCS",
            "target": "TEMPORARY_POSTGRES_ONLY",
            "live_staging_inventory_changed": False,
        }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if not safe_target(settings):
        print(json.dumps({"status": "BLOCKED", "reason": "not_an_isolated_test_database"}))
        raise SystemExit(2)
    if not args.apply:
        print(json.dumps({"status": "DRY_RUN", "changes": False}))
        return
    try:
        result = asyncio.run(scenario(settings))
    except Exception as error:
        print(json.dumps({"status": "FAILED", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
