"""Read-only staging preflight for Mini App, warehouse and real-photo acceptance.

Reports aggregate, non-sensitive counts and whether optional write surfaces
remain disabled. Does not activate Mini App, run inference, create drafts,
change the database, read image bytes, or certify device/photo accuracy.
"""

import asyncio
import json
from dataclasses import asdict, dataclass

from sqlalchemy import func, select

from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    InventoryBalance,
    InventoryProductUnit,
    InventoryReservation,
    InventoryWarehouse,
    Product,
    ProductImageRelation,
)
from office_assistant.infrastructure.database.session import create_session_factory


@dataclass(frozen=True, slots=True)
class GateCounts:
    active_warehouses: int
    approved_product_units: int
    known_stock_balances: int
    active_reservations: int
    active_confirmed_photo_relations: int


async def read_counts(settings: Settings) -> GateCounts:
    """Only aggregate SELECTs. Never print product identities or documents."""

    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            warehouses = await session.scalar(
                select(func.count()).select_from(InventoryWarehouse).where(
                    InventoryWarehouse.status == "ACTIVE"
                )
            )
            units = await session.scalar(select(func.count()).select_from(InventoryProductUnit))
            balances = await session.scalar(select(func.count()).select_from(InventoryBalance))
            reservations = await session.scalar(
                select(func.count()).select_from(InventoryReservation).where(
                    InventoryReservation.status == "ACTIVE"
                )
            )
            references = await session.scalar(
                select(func.count())
                .select_from(ProductImageRelation)
                .join(Product, Product.id == ProductImageRelation.product_id)
                .where(
                    ProductImageRelation.confirmation_type == "USER_EXPLICIT",
                    ProductImageRelation.superseded_at.is_(None),
                    Product.is_active.is_(True),
                )
            )
            return GateCounts(
                active_warehouses=int(warehouses or 0),
                approved_product_units=int(units or 0),
                known_stock_balances=int(balances or 0),
                active_reservations=int(reservations or 0),
                active_confirmed_photo_relations=int(references or 0),
            )
    finally:
        await engine.dispose()


def build_report(settings: Settings, counts: GateCounts) -> dict[str, object]:
    """No automated PASS claims for real phones, writes, or labelled photos."""

    origin = settings.miniapp_public_origin or ""
    https_origin = origin.startswith("https://") and bool(origin.removeprefix("https://"))
    return {
        "status": "PREFLIGHT_ONLY",
        "miniapp": {
            "enabled": settings.miniapp_enabled,
            "https_origin_configured": https_origin,
            "stock_transfer_enabled": settings.miniapp_stock_transfer_enabled,
            "stock_actions_enabled": settings.miniapp_stock_actions_enabled,
            "stock_xlsx_enabled": settings.miniapp_stock_xlsx_enabled,
            "real_telegram_mobile_accepted": False,
            "gate": "DEVICE_HTTPS_UAT_REQUIRED"
            if settings.miniapp_enabled and https_origin
            else "NOT_ENABLED_FOR_DEVICE_TEST",
        },
        "inventory": {
            "active_warehouses": counts.active_warehouses,
            "approved_product_units": counts.approved_product_units,
            "known_stock_balances": counts.known_stock_balances,
            "active_reservations": counts.active_reservations,
            "live_mutation_accepted": False,
            "gate": "ISOLATED_OPERATOR_UAT_REQUIRED",
        },
        "photo_recognition": {
            "active_confirmed_reference_relations": counts.active_confirmed_photo_relations,
            "real_holdout_benchmark_accepted": False,
            "gate": "REAL_LABELLED_PHOTOS_REQUIRED",
        },
    }


def main() -> None:
    settings = load_settings()
    try:
        counts = asyncio.run(read_counts(settings))
    except Exception as error:
        # Credentials, source paths and customer data must not enter CLI logs.
        print(json.dumps({"status": "PREFLIGHT_BLOCKED", "error_type": type(error).__name__}))
        raise SystemExit(2) from None
    print(json.dumps(build_report(settings, counts), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
