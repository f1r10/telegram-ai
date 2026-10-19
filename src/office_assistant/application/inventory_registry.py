"""Admin-approved location and product unit registries (not automatic stock writes).

No existing unitless ledger row is inferred to be PCS/KG/BOX. An explicit
operator must reconcile historical stock before declaring a canonical unit.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.inventory import (
    InventoryConflict,
    InventoryLedgerService,
    InventoryValidationError,
    _key,
)
from office_assistant.business.normalization import normalize_product_code
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    InventoryBalance,
    InventoryMovement,
    InventoryProductUnit,
    InventoryReservation,
    InventoryUnitConversion,
    InventoryWarehouse,
    Product,
    User,
)
from office_assistant.repositories.products import ProductRepository

VALID_UNITS = frozenset({"PCS", "KG", "L", "BOX"})
_MAX_QUANTITY = Decimal("999999999999999.999")
_THOUSANDTH = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class WarehouseView:
    name: str
    key: str
    status: str


@dataclass(frozen=True, slots=True)
class ProductUnitView:
    code: str
    base_unit: str


def _unit(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in VALID_UNITS:
        raise InventoryValidationError("Ölçü vahidi naməlumdur; təsdiqlənmiş vahid tələb olunur.")
    return normalized


def _quantity(value: Decimal, *, allow_zero: bool = False) -> Decimal:
    if not value.is_finite() or value < 0 or (value == 0 and not allow_zero):
        raise InventoryValidationError("Miqdar mənfi, sıfır və ya qeyri-müəyyən ola bilməz.")
    if value > _MAX_QUANTITY or value.as_tuple().exponent < -3:
        raise InventoryValidationError("Miqdar limit və üç onluq dəqiqliyini aşır.")
    return value


class InventoryRegistryService:
    """Read and approve fixed warehouse/unit policies; never auto-convert stock."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    @staticmethod
    async def _locked_product(session: AsyncSession, code: str) -> Product:
        product = await InventoryLedgerService._product(session, normalize_product_code(code))
        locked = await ProductRepository(session).get(product.id, for_update=True)
        if locked is None or not locked.is_active:
            raise InventoryConflict("Aktiv məhsul tapılmadı.")
        return locked

    async def register_warehouse(self, actor: User, name: str) -> WarehouseView:
        key = _key(name)
        if not 1 <= len(key) <= 128 or not 1 <= len(name.strip()) <= 128:
            raise InventoryValidationError("Anbar adı düzgün deyil.")
        if any(ord(char) < 32 for char in name):
            raise InventoryValidationError("Anbar adında gizli simvollar olmaz.")
        try:
            async with self.session_factory.begin() as session:
                await InventoryLedgerService._authorize(session, actor, admin=True)
                current = await session.get(InventoryWarehouse, key, with_for_update=True)
                if current is not None:
                    raise InventoryConflict("Bu anbar artıq qeydiyyatdadır.")
                # Existing unregistered rows are NOT automatically considered verified
                # warehouse openings. They must be reconciled by an operator.
                legacy = await session.scalar(
                    select(InventoryBalance.id).where(InventoryBalance.location_key == key)
                )
                if legacy is not None:
                    raise InventoryConflict("Bu anbarın tarixi qalığı ayrıca yoxlanmalıdır.")
                row = InventoryWarehouse(
                    location_key=key,
                    location_name=name.strip(),
                    status="ACTIVE",
                    approved_by_user_id=actor.id,
                )
                session.add(row)
                session.add(
                    AuditEvent(
                        actor_user_id=actor.id,
                        action="INVENTORY_WAREHOUSE_REGISTERED",
                        entity_type="inventory_warehouse",
                        entity_id=key,
                        event_metadata={"location_key": key},
                    )
                )
                return WarehouseView(name.strip(), key, "ACTIVE")
        except IntegrityError as error:
            raise InventoryConflict("Anbar reyestrində təkrar qeydiyyat var.") from error

    async def list_warehouses(
        self, actor: User, *, limit: int = 100
    ) -> tuple[WarehouseView, ...]:
        if not 1 <= limit <= 100:
            raise InventoryValidationError("Anbar siyahısı limiti düzgün deyil.")
        async with self.session_factory() as session:
            await InventoryLedgerService._authorize(session, actor)
            rows = (
                await session.scalars(
                    select(InventoryWarehouse)
                    .order_by(InventoryWarehouse.location_key)
                    .limit(limit)
                )
            ).all()
            return tuple(
                WarehouseView(row.location_name, row.location_key, row.status) for row in rows
            )

    async def declare_unit(
        self, actor: User, *, product_code: str, base_unit: str
    ) -> ProductUnitView:
        unit = _unit(base_unit)
        try:
            async with self.session_factory.begin() as session:
                await InventoryLedgerService._authorize(session, actor, admin=True)
                product = await self._locked_product(session, product_code)
                if await session.get(InventoryProductUnit, product.id) is not None:
                    raise InventoryConflict("Məhsulun əsas ölçü vahidi artıq təsdiqlənib.")
                # No speculative backfill: existing movements/reservations/balances
                # have UNKNOWN units and must not be silently labeled as PCS.
                for table in (InventoryBalance, InventoryMovement, InventoryReservation):
                    exists = await session.scalar(
                        select(table.id).where(table.product_id == product.id).limit(1)
                    )
                    if exists is not None:
                        raise InventoryConflict(
                            "Mövcud stokun ölçü vahidi məlum deyil; əvvəl audit tələb olunur."
                        )
                session.add(
                    InventoryProductUnit(
                        product_id=product.id, base_unit=unit, approved_by_user_id=actor.id
                    )
                )
                session.add(
                    AuditEvent(
                        actor_user_id=actor.id,
                        action="INVENTORY_BASE_UNIT_APPROVED",
                        entity_type="product",
                        entity_id=str(product.id),
                        event_metadata={"base_unit": unit},
                    )
                )
                return ProductUnitView(product.display_code, unit)
        except IntegrityError as error:
            raise InventoryConflict("Məhsul ölçü vahidi artıq təsdiqlənib.") from error

    async def declare_conversion(
        self, actor: User, *, product_code: str, from_unit: str, factor: Decimal
    ) -> None:
        unit = _unit(from_unit)
        _quantity(factor)
        try:
            async with self.session_factory.begin() as session:
                await InventoryLedgerService._authorize(session, actor, admin=True)
                product = await self._locked_product(session, product_code)
                base = await session.get(InventoryProductUnit, product.id)
                if base is None or base.base_unit == unit:
                    raise InventoryConflict("Məhsulun fərqli təsdiqlənmiş əsas vahidi lazımdır.")
                prior = await session.scalar(
                    select(InventoryUnitConversion.id).where(
                        InventoryUnitConversion.product_id == product.id,
                        InventoryUnitConversion.from_unit == unit,
                    )
                )
                if prior is not None:
                    raise InventoryConflict("Bu vahidin çevirmə əmsalı artıq təsdiqlənib.")
                session.add(
                    InventoryUnitConversion(
                        product_id=product.id,
                        from_unit=unit,
                        factor=factor,
                        approved_by_user_id=actor.id,
                    )
                )
                session.add(
                    AuditEvent(
                        actor_user_id=actor.id,
                        action="INVENTORY_UNIT_CONVERSION_APPROVED",
                        entity_type="product",
                        entity_id=str(product.id),
                        event_metadata={"from_unit": unit, "to_unit": base.base_unit, "factor": str(factor)},
                    )
                )
        except IntegrityError as error:
            raise InventoryConflict("Vahid çevirməsi artıq təsdiqlənib.") from error

    async def convert(
        self, actor: User, *, product_code: str, quantity: Decimal, from_unit: str
    ) -> tuple[Decimal, str]:
        """Return exact base quantity only when an admin approved the specific factor."""
        unit = _unit(from_unit)
        _quantity(quantity, allow_zero=True)
        async with self.session_factory() as session:
            await InventoryLedgerService._authorize(session, actor)
            product = await InventoryLedgerService._product(
                session, normalize_product_code(product_code)
            )
            base = await session.get(InventoryProductUnit, product.id)
            if base is None:
                raise InventoryConflict("Məhsulun təsdiqlənmiş əsas vahidi yoxdur.")
            if base.base_unit == unit:
                return quantity, base.base_unit
            rule = await session.scalar(
                select(InventoryUnitConversion).where(
                    InventoryUnitConversion.product_id == product.id,
                    InventoryUnitConversion.from_unit == unit,
                )
            )
            if rule is None:
                raise InventoryConflict("Uyğun təsdiqlənmiş ölçü çevirməsi yoxdur.")
            try:
                with localcontext() as context:
                    context.prec = 40
                    converted = quantity * rule.factor
                    if converted != converted.quantize(_THOUSANDTH):
                        raise InventoryValidationError(
                            "Çevrilmiş miqdar üç onluq dəqiqliyə sığmır."
                        )
                return _quantity(converted, allow_zero=True), base.base_unit
            except InvalidOperation as error:
                raise InventoryValidationError("Miqdar çevirməsi etibarsızdır.") from error
