"""Explicit, approval-gated inventory ledger; no catalog evidence implies stock.

A missing opening balance means UNKNOWN, never zero. Sale invoices, quotes,
payments, file uploads and model responses never post stock automatically.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.errors import AuthorizationDenied
from office_assistant.business.normalization import normalize_product_alias, normalize_product_code
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    InventoryBalance,
    InventoryMovement,
    InventoryProductUnit,
    InventoryReservation,
    InventoryWarehouse,
    Product,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.repositories.products import ProductRepository

_WRITE_PREFIX = re.compile(r"^\s*anbar əməliyyatı\s*[:：]\s*(.*)$", re.I | re.S)
_STOCK_PREFIX = re.compile(r"^\s*(?:stok|anbar qalığı)\s*[:：]\s*(.*)$", re.I | re.S)
_KINDS = {"AÇILIŞ": "OPENING", "GİRİŞ": "RECEIPT", "ÇIXIŞ": "ISSUE"}
_NATURAL_STOCK = re.compile(
    r"^\s*(?P<code>[A-Za-z0-9][A-Za-z0-9_./-]{1,63})\s+"
    r"(?:stokda\s+varm[ıi]|anbarda\s+varm[ıi]|"
    r"stok\s+qal[ıi]ğ[ıi]\s+neç[əe]dir|"
    r"neç[əe]\s+[əe]d[əe]d\s+qal[ıi]b)\s*[?!.]*\s*$",
    re.I,
)


def parse_natural_stock_request(text: str) -> str | None:
    """Only clearly identified product-code questions qualify as actual stock inquiries.

    Never interpret generic catalog discussion or an unknown product name as
    a stock code; leave those utterances for normal grounded document search.
    """

    match = _NATURAL_STOCK.fullmatch(text)
    if match is None or not any(char.isdigit() for char in match.group("code")):
        return None
    try:
        return normalize_product_code(match.group("code"))
    except ValueError:
        return None


_MAX_QUANTITY = Decimal("999999999999999.999")


class InventoryValidationError(ValueError):
    pass


class InventoryConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StockDraft:
    product_code: str
    warehouse: str
    kind: str
    quantity: Decimal
    reference: str
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class StockTransferDraft:
    product_code: str
    source_warehouse: str
    destination_warehouse: str
    quantity: Decimal
    reference: str
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class StockTransferReceipt:
    product_code: str
    source_warehouse: str
    destination_warehouse: str
    source_balance_after: Decimal
    destination_balance_after: Decimal
    quantity: Decimal
    reference: str


@dataclass(frozen=True, slots=True)
class StockAdjustmentDraft:
    product_code: str
    warehouse: str
    kind: str
    quantity: Decimal
    reference: str
    reason: str
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class StockAdjustmentReceipt:
    product_code: str
    warehouse: str
    kind: str
    balance_before: Decimal
    balance_after: Decimal
    applied_delta: Decimal
    reference: str


@dataclass(frozen=True, slots=True)
class StockReservationDraft:
    product_code: str
    warehouse: str
    quantity: Decimal
    reference: str
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class StockReservationReceipt:
    product_code: str
    warehouse: str
    reference: str
    quantity: Decimal
    status: str
    on_hand: Decimal
    reserved: Decimal
    available: Decimal


@dataclass(frozen=True, slots=True)
class StockEntry:
    product_code: str
    warehouse: str
    kind: str
    quantity: Decimal
    balance_after: Decimal
    reference: str


@dataclass(frozen=True, slots=True)
class StockMovementHistoryEntry:
    product_code: str
    warehouse: str
    kind: str
    quantity: Decimal
    balance_after: Decimal
    document_reference: str
    confirmed_at: datetime


@dataclass(frozen=True, slots=True)
class StockLocation:
    warehouse: str
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class StockSnapshot:
    product_code: str
    locations: tuple[StockLocation, ...]
    total: Decimal | None
    unit_code: str | None = None


def _key(text: str) -> str:
    return " ".join(text.casefold().split())


def parse_stock_draft(text: str) -> StockDraft | None:
    """No unapproved conversational message can modify inventory."""

    match = _WRITE_PREFIX.fullmatch(text)
    if match is None:
        return None
    fields = [part.strip() for part in match.group(1).split("|")]
    if len(fields) != 5:
        raise InventoryValidationError(
            "Format: Anbar əməliyyatı: KA45 | Əsas | AÇILIŞ/GİRİŞ/ÇIXIŞ | 10 | SƏNƏD-001"
        )
    raw_code, warehouse, raw_kind, raw_quantity, reference = fields
    try:
        code = normalize_product_code(raw_code)
    except ValueError as error:
        raise InventoryValidationError("Məhsul kodu düzgün deyil.") from error
    if not code or len(code) > 128 or not 1 <= len(warehouse) <= 128:
        raise InventoryValidationError("Məhsul və anbar adı düzgün olmalıdır.")
    if not 1 <= len(reference) <= 128 or not _key(reference):
        raise InventoryValidationError("Əməliyyat üçün unikal sənəd nömrəsi lazımdır.")
    kind = _KINDS.get(raw_kind.upper())
    if kind is None:
        raise InventoryValidationError("Əməliyyat AÇILIŞ, GİRİŞ və ya ÇIXIŞ olmalıdır.")
    try:
        quantity = Decimal(raw_quantity)
    except InvalidOperation as error:
        raise InventoryValidationError("Miqdar düzgün deyil.") from error
    if (
        not quantity.is_finite()
        or quantity < 0
        or quantity > _MAX_QUANTITY
        or (kind != "OPENING" and quantity == 0)
    ):
        raise InventoryValidationError("Miqdar limit daxilində müsbət olmalıdır.")
    scale = quantity.as_tuple().exponent
    if not isinstance(scale, int) or scale < -3:
        raise InventoryValidationError("Miqdar ən çox üç onluq rəqəmlə yazılmalıdır.")
    return StockDraft(code, warehouse, kind, quantity, reference)


def parse_stock_request(text: str) -> str | None:
    match = _STOCK_PREFIX.fullmatch(text)
    if match is None:
        return None
    code = match.group(1).strip()
    if not code or len(code) > 128:
        raise InventoryValidationError("Məhsul kodunu yazın. Məsələn: Stok: KA45")
    try:
        return normalize_product_code(code)
    except ValueError as error:
        raise InventoryValidationError("Məhsul kodu düzgün deyil.") from error


class InventoryLedgerService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    @staticmethod
    async def _authorize(session: AsyncSession, actor: User, *, admin: bool = False) -> None:
        db_user = await session.scalar(
            select(User).where(
                User.id == actor.id,
                User.telegram_user_id == actor.telegram_user_id,
                User.status == UserStatus.ACTIVE,
                User.role.in_([UserRole.ADMIN, UserRole.USER]),
            )
        )
        if db_user is None or (admin and db_user.role != UserRole.ADMIN):
            raise AuthorizationDenied("Inventory access is restricted to active approved users")

    @staticmethod
    async def _product(session: AsyncSession, code: str) -> Product:
        repo = ProductRepository(session)
        product = await repo.find_by_code(code)
        if product is None:
            product = await repo.find_by_alias(normalize_product_alias(code))
        if product is None:
            raise InventoryValidationError(
                "Aktiv kataloqda bu məhsul kodu tapılmadı; stok yazılmadı."
            )
        return product

    @staticmethod
    async def _reserved_quantity(
        session: AsyncSession, product_id: UUID, location_key: str
    ) -> Decimal:
        amount = await session.scalar(
            select(func.sum(InventoryReservation.quantity)).where(
                InventoryReservation.product_id == product_id,
                InventoryReservation.location_key == location_key,
                InventoryReservation.status == "ACTIVE",
            )
        )
        return Decimal(str(amount)) if amount is not None else Decimal("0")

    @staticmethod
    async def _enforce_unit_policy(
        session: AsyncSession, product_id: UUID, supplied_unit: str | None,
        locations: tuple[str, ...],
    ) -> None:
        """Registered products require an explicit canonical unit and open warehouse."""
        rule = await session.get(InventoryProductUnit, product_id)
        if rule is None:
            if supplied_unit is not None:
                raise InventoryValidationError(
                    "Bu məhsulun əsas vahidi təsdiqlənməyib; vahidli əməliyyat dayandırıldı."
                )
            return  # legacy unit-UNKNOWN records stay unchanged until owner reconciliation
        if supplied_unit is None or supplied_unit.strip().upper() != rule.base_unit:
            raise InventoryValidationError(
                "Ölçü vahidi uyğun deyil; yalnız məhsulun təsdiqlənmiş əsas vahidi qəbul edilir."
            )
        for location_key in set(locations):
            warehouse = await session.get(InventoryWarehouse, location_key)
            if warehouse is None or warehouse.status != "ACTIVE":
                raise InventoryConflict(
                    "Yalnız reyestrdə aktiv təsdiqlənmiş anbarda əməliyyat olar."
                )

    async def snapshot(self, actor: User, code: str) -> StockSnapshot:
        async with self.session_factory() as session:
            await self._authorize(session, actor)
            product = await self._product(session, normalize_product_code(code))
            unit_policy = await session.get(InventoryProductUnit, product.id)
            rows = list(
                (
                    await session.scalars(
                        select(InventoryBalance)
                        .where(InventoryBalance.product_id == product.id)
                        .order_by(InventoryBalance.location_key)
                    )
                ).all()
            )
            return StockSnapshot(
                product_code=product.display_code,
                locations=tuple(StockLocation(item.location_name, item.quantity) for item in rows),
                total=sum((item.quantity for item in rows), Decimal(0)) if rows else None,
                unit_code=unit_policy.base_unit if unit_policy is not None else None,
            )

    async def history(
        self, actor: User, code: str, *, warehouse: str | None = None, limit: int = 30
    ) -> tuple[StockMovementHistoryEntry, ...]:
        """Read real approved movements only; never derive entries from raw documents."""
        if not 1 <= limit <= 100:
            raise InventoryValidationError("Tarixçə limiti 1–100 arasında olmalıdır.")
        key = _key(warehouse) if warehouse is not None else None
        if warehouse is not None and (key is None or not 1 <= len(key) <= 128):
            raise InventoryValidationError("Anbar adı düzgün deyil.")
        async with self.session_factory() as session:
            await self._authorize(session, actor)
            product = await self._product(session, normalize_product_code(code))
            query = select(InventoryMovement).where(InventoryMovement.product_id == product.id)
            if key is not None:
                query = query.where(InventoryMovement.location_key == key)
            rows = list(
                (
                    await session.scalars(
                        query.order_by(
                            InventoryMovement.created_at.desc(),
                            InventoryMovement.id.desc(),
                        ).limit(limit)
                    )
                ).all()
            )
            return tuple(
                StockMovementHistoryEntry(
                    product.display_code,
                    entry.location_name,
                    entry.kind,
                    entry.quantity,
                    entry.balance_after,
                    entry.reference_label,
                    entry.created_at,
                )
                for entry in rows
            )

    async def record(self, actor: User, draft: StockDraft) -> StockEntry:
        if draft.kind not in {"OPENING", "RECEIPT", "ISSUE"}:
            raise InventoryValidationError("Naməlum anbar əməliyyatı.")
        scale = draft.quantity.as_tuple().exponent
        if (
            not draft.quantity.is_finite()
            or draft.quantity < 0
            or draft.quantity > _MAX_QUANTITY
            or not isinstance(scale, int)
            or scale < -3
            or (draft.kind != "OPENING" and draft.quantity == 0)
        ):
            raise InventoryValidationError("Miqdar düzgün deyil.")
        key = _key(draft.warehouse)
        reference_key = _key(draft.reference)
        if reference_key.startswith(("xfer:", "adj:", "res:")):
            raise InventoryValidationError(
                "Daxili köçürmə/düzəliş nömrəsi adi giriş/çıxışda işlədilə bilməz."
            )
        if not 1 <= len(key) <= 128 or not 1 <= len(reference_key) <= 128:
            raise InventoryValidationError("Anbar və sənəd nömrəsi düzgün deyil.")
        try:
            async with self.session_factory.begin() as session:
                await self._authorize(session, actor, admin=True)
                product = await self._product(session, normalize_product_code(draft.product_code))
                # Serializes first-ever opening rows as well as subsequent changes
                # to an existing warehouse; no lost balances under concurrent writes.
                locked = await ProductRepository(session).get(product.id, for_update=True)
                if locked is None or not locked.is_active:
                    raise InventoryConflict("Məhsul artıq aktiv deyil.")
                await self._enforce_unit_policy(session, product.id, draft.unit, (key,))
                previous = await session.scalar(
                    select(InventoryMovement.id).where(
                        InventoryMovement.product_id == product.id,
                        InventoryMovement.location_key == key,
                        InventoryMovement.reference_key == reference_key,
                    )
                )
                if previous is not None:
                    raise InventoryConflict("Bu sənəd nömrəsi ilə anbar əməliyyatı artıq var.")
                balance = await session.scalar(
                    select(InventoryBalance)
                    .where(
                        InventoryBalance.product_id == product.id,
                        InventoryBalance.location_key == key,
                    )
                    .with_for_update()
                )
                if balance is None and draft.kind != "OPENING":
                    raise InventoryConflict(
                        "Anbar qalığı naməlumdur. Əvvəlcə təsdiqlənmiş AÇILIŞ qalığı daxil edin."
                    )
                if balance is not None and draft.kind == "OPENING":
                    raise InventoryConflict(
                        "Bu anbar üçün açılış qalığı artıq yazılıb; GİRİŞ/ÇIXIŞ istifadə edin."
                    )
                before = balance.quantity if balance is not None else Decimal(0)
                after = (
                    draft.quantity
                    if draft.kind == "OPENING"
                    else before + (draft.quantity if draft.kind == "RECEIPT" else -draft.quantity)
                )
                if after < 0:
                    raise InventoryConflict("Anbarda kifayət qədər təsdiqlənmiş qalıq yoxdur.")
                if draft.kind == "ISSUE":
                    reserved = await self._reserved_quantity(session, product.id, key)
                    if after < reserved:
                        raise InventoryConflict("Rezerv edilmiş stokdan çıxış etmək olmaz.")
                if after > _MAX_QUANTITY:
                    raise InventoryConflict("Anbar qalığı sistem limitini aşır.")
                if balance is None:
                    session.add(
                        InventoryBalance(
                            product_id=product.id,
                            location_key=key,
                            location_name=draft.warehouse.strip(),
                            quantity=after,
                        )
                    )
                else:
                    balance.quantity = after
                movement = InventoryMovement(
                    product_id=product.id,
                    location_key=key,
                    location_name=draft.warehouse.strip(),
                    kind=draft.kind,
                    quantity=draft.quantity,
                    balance_after=after,
                    reference_key=reference_key,
                    reference_label=draft.reference.strip(),
                    approved_by_user_id=actor.id,
                )
                session.add(movement)
                await session.flush()
                session.add(
                    AuditEvent(
                        actor_user_id=actor.id,
                        action="INVENTORY_MOVEMENT_POSTED",
                        entity_type="inventory_movement",
                        entity_id=str(movement.id),
                        event_metadata={
                            "product_id": str(product.id),
                            "location_key": key,
                            "reference_key": reference_key,
                            "kind": draft.kind,
                        },
                    )
                )
                return StockEntry(
                    product.display_code,
                    draft.warehouse,
                    draft.kind,
                    draft.quantity,
                    after,
                    draft.reference,
                )
        except IntegrityError as error:
            raise InventoryConflict("Təkrar stok əməliyyatı; heç nə iki dəfə yazılmadı.") from error

    async def record_batch(
        self, actor: User, drafts: tuple[StockDraft, ...], *, workbook_sha256: str
    ) -> tuple[StockEntry, ...]:
        """All-or-nothing admin-approved XLSX journal after digest confirmation.

        Product locks are acquired in canonical sorted order before any write;
        a bad final row rolls the whole batch back, including audit events.
        """
        if not 1 <= len(drafts) <= 200 or re.fullmatch(r"[0-9a-f]{64}", workbook_sha256) is None:
            raise InventoryValidationError("Toplu stok üçün limit və təsdiq düzgün deyil.")
        checked: list[tuple[str, str, str, StockDraft]] = []
        seen: set[tuple[str, str, str]] = set()
        for draft in drafts:
            try:
                code = normalize_product_code(draft.product_code)
            except ValueError as error:
                raise InventoryValidationError("Məhsul kodu düzgün deyil.") from error
            location = _key(draft.warehouse)
            reference = _key(draft.reference)
            precision = draft.quantity.as_tuple().exponent
            if (
                draft.kind not in {"OPENING", "RECEIPT", "ISSUE"}
                or not 1 <= len(location) <= 128
                or not 1 <= len(reference) <= 128
                or reference.startswith(("xfer:", "adj:", "res:"))
                or not draft.quantity.is_finite()
                or draft.quantity < 0
                or draft.quantity > _MAX_QUANTITY
                or (draft.quantity == 0 and draft.kind != "OPENING")
                or not isinstance(precision, int)
                or precision < -3
            ):
                raise InventoryValidationError("Toplu stok sətrində təsdiqsiz məlumat var.")
            key = (code, location, reference)
            if key in seen:
                raise InventoryValidationError("Təkrarlanan stok sənəd nömrəsi var.")
            seen.add(key)
            checked.append((code, location, reference, draft))
        try:
            async with self.session_factory.begin() as session:
                await self._authorize(session, actor, admin=True)
                # Different aliases can name the same product. Resolve first,
                # then lock canonical product IDs in one deterministic order.
                resolved = {
                    code: await self._product(session, code)
                    for code in sorted({row[0] for row in checked})
                }
                locked_by_id: dict[UUID, Product] = {}
                for product_id in sorted({item.id for item in resolved.values()}, key=str):
                    locked = await ProductRepository(session).get(product_id, for_update=True)
                    if locked is None or not locked.is_active:
                        raise InventoryConflict("Məhsul artıq aktiv deyil.")
                    locked_by_id[product_id] = locked
                products = {code: locked_by_id[product.id] for code, product in resolved.items()}

                receipt: list[StockEntry] = []
                for code, location, reference, draft in checked:
                    product = products[code]
                    await self._enforce_unit_policy(
                        session, product.id, draft.unit, (location,)
                    )
                    prior = await session.scalar(
                        select(InventoryMovement.id).where(
                            InventoryMovement.product_id == product.id,
                            InventoryMovement.location_key == location,
                            InventoryMovement.reference_key == reference,
                        )
                    )
                    if prior is not None:
                        raise InventoryConflict("Toplu faylda əvvəldən işlənmiş sənəd var.")
                    balance = await session.scalar(
                        select(InventoryBalance)
                        .where(
                            InventoryBalance.product_id == product.id,
                            InventoryBalance.location_key == location,
                        )
                        .with_for_update()
                    )
                    if balance is None and draft.kind != "OPENING":
                        raise InventoryConflict("Əvvəl təsdiqlənmiş açılış qalığı tələb olunur.")
                    if balance is not None and draft.kind == "OPENING":
                        raise InventoryConflict("Bu anbarda açılış artıq təsdiqlənib.")
                    before = balance.quantity if balance is not None else Decimal(0)
                    after = (
                        draft.quantity
                        if draft.kind == "OPENING"
                        else before
                        + (draft.quantity if draft.kind == "RECEIPT" else -draft.quantity)
                    )
                    if not 0 <= after <= _MAX_QUANTITY:
                        raise InventoryConflict("Toplu əməliyyat stok qalıq limitini pozur.")
                    if draft.kind == "ISSUE":
                        reserved = await self._reserved_quantity(session, product.id, location)
                        if after < reserved:
                            raise InventoryConflict("Rezerv edilmiş stokdan çıxış etmək olmaz.")
                    if balance is None:
                        session.add(
                            InventoryBalance(
                                product_id=product.id,
                                location_key=location,
                                location_name=draft.warehouse.strip(),
                                quantity=after,
                            )
                        )
                    else:
                        balance.quantity = after
                    movement = InventoryMovement(
                        product_id=product.id,
                        location_key=location,
                        location_name=draft.warehouse.strip(),
                        kind=draft.kind,
                        quantity=draft.quantity,
                        balance_after=after,
                        reference_key=reference,
                        reference_label=draft.reference.strip(),
                        approved_by_user_id=actor.id,
                    )
                    session.add(movement)
                    await session.flush()
                    session.add(
                        AuditEvent(
                            actor_user_id=actor.id,
                            action="INVENTORY_BULK_MOVEMENT_POSTED",
                            entity_type="inventory_movement",
                            entity_id=str(movement.id),
                            event_metadata={
                                "product_id": str(product.id),
                                "kind": draft.kind,
                                "location_key": location,
                                "reference_key": reference,
                                "source_sha256": workbook_sha256,
                            },
                        )
                    )
                    receipt.append(
                        StockEntry(
                            product.display_code,
                            draft.warehouse,
                            draft.kind,
                            draft.quantity,
                            after,
                            draft.reference,
                        )
                    )
                return tuple(receipt)
        except IntegrityError as error:
            raise InventoryConflict("Toplu əməliyyat dəyişmədən rədd edildi.") from error

    async def transfer(self, actor: User, draft: StockTransferDraft) -> StockTransferReceipt:
        """Move confirmed stock between two already opened warehouses atomically.

        This low-level API deliberately has no unreviewed Telegram intent route:
        an admin must explicitly approve any future UI transfer preview first.
        """
        source_key = _key(draft.source_warehouse)
        dest_key = _key(draft.destination_warehouse)
        reference_key = "xfer:" + _key(draft.reference)
        scale = draft.quantity.as_tuple().exponent
        if (
            not 1 <= len(source_key) <= 128
            or not 1 <= len(dest_key) <= 128
            or source_key == dest_key
            or not 1 <= len(_key(draft.reference)) <= 123
            or not draft.quantity.is_finite()
            or draft.quantity <= 0
            or draft.quantity > _MAX_QUANTITY
            or not isinstance(scale, int)
            or scale < -3
        ):
            raise InventoryValidationError(
                "Köçürmə miqdarı, anbarlar və sənəd nömrəsi düzgün deyil."
            )
        try:
            code = normalize_product_code(draft.product_code)
        except ValueError as error:
            raise InventoryValidationError("Məhsul kodu düzgün deyil.") from error
        try:
            async with self.session_factory.begin() as session:
                await self._authorize(session, actor, admin=True)
                product = await self._product(session, code)
                locked = await ProductRepository(session).get(product.id, for_update=True)
                if locked is None or not locked.is_active:
                    raise InventoryConflict("Məhsul artıq aktiv deyil.")
                await self._enforce_unit_policy(
                    session, product.id, draft.unit, (source_key, dest_key)
                )
                # The product row serializes concurrent operations, even when the
                # destination has no row yet. Both legs stay in one transaction.
                balances = list(
                    (
                        await session.scalars(
                            select(InventoryBalance)
                            .where(
                                InventoryBalance.product_id == product.id,
                                InventoryBalance.location_key.in_([source_key, dest_key]),
                            )
                            .order_by(InventoryBalance.location_key)
                            .with_for_update()
                        )
                    ).all()
                )
                by_location = {balance.location_key: balance for balance in balances}
                source = by_location.get(source_key)
                destination = by_location.get(dest_key)
                if source is None or destination is None:
                    raise InventoryConflict("Hər iki anbarda təsdiqlənmiş açılış qalığı olmalıdır.")
                prior = await session.scalar(
                    select(InventoryMovement.id).where(
                        InventoryMovement.product_id == product.id,
                        InventoryMovement.reference_key == reference_key,
                    )
                )
                if prior is not None:
                    raise InventoryConflict("Bu köçürmə sənədi artıq istifadə olunub.")
                if source.quantity < draft.quantity:
                    raise InventoryConflict("Mənbə anbarda kifayət qədər qalıq yoxdur.")
                active_reserved = await self._reserved_quantity(session, product.id, source_key)
                if source.quantity - draft.quantity < active_reserved:
                    raise InventoryConflict("Köçürmə rezerv edilmiş miqdarı poza bilməz.")
                if destination.quantity + draft.quantity > _MAX_QUANTITY:
                    raise InventoryConflict("Təyinat anbarı qalıq limitini aşır.")
                source.quantity -= draft.quantity
                destination.quantity += draft.quantity
                leaving = InventoryMovement(
                    product_id=product.id,
                    location_key=source_key,
                    location_name=source.location_name,
                    kind="ISSUE",
                    quantity=draft.quantity,
                    balance_after=source.quantity,
                    reference_key=reference_key,
                    reference_label=draft.reference.strip(),
                    approved_by_user_id=actor.id,
                )
                arriving = InventoryMovement(
                    product_id=product.id,
                    location_key=dest_key,
                    location_name=destination.location_name,
                    kind="RECEIPT",
                    quantity=draft.quantity,
                    balance_after=destination.quantity,
                    reference_key=reference_key,
                    reference_label=draft.reference.strip(),
                    approved_by_user_id=actor.id,
                )
                session.add_all([leaving, arriving])
                await session.flush()
                session.add(
                    AuditEvent(
                        actor_user_id=actor.id,
                        action="INVENTORY_TRANSFER_POSTED",
                        entity_type="inventory_movement",
                        entity_id=str(leaving.id),
                        event_metadata={
                            "product_id": str(product.id),
                            "from": source_key,
                            "to": dest_key,
                            "reference_key": reference_key,
                            "out_id": leaving.id,
                            "in_id": arriving.id,
                        },
                    )
                )
                return StockTransferReceipt(
                    product.display_code,
                    source.location_name,
                    destination.location_name,
                    source.quantity,
                    destination.quantity,
                    draft.quantity,
                    draft.reference,
                )
        except IntegrityError as error:
            raise InventoryConflict("Təkrar köçürmə: heç nə iki dəfə yazılmadı.") from error

    async def adjust(self, actor: User, draft: StockAdjustmentDraft) -> StockAdjustmentReceipt:
        """Audited return/damage/write-off/correction without editing prior movements.

        This domain method is intentionally not reachable from free-form chat:
        the future admin UI must perform explicit preview and confirmation.
        """
        allowed = {"RETURN", "DAMAGE", "WRITE_OFF", "CORRECTION"}
        location = _key(draft.warehouse)
        normalized_ref = _key(draft.reference)
        reason = " ".join(draft.reason.split())
        scale = draft.quantity.as_tuple().exponent
        if (
            draft.kind not in allowed
            or not 1 <= len(location) <= 128
            or not 1 <= len(normalized_ref) <= 124
            or not 10 <= len(reason) <= 300
            or not draft.quantity.is_finite()
            or draft.quantity == 0
            or abs(draft.quantity) > _MAX_QUANTITY
            or not isinstance(scale, int)
            or scale < -3
            or (draft.kind != "CORRECTION" and draft.quantity < 0)
        ):
            raise InventoryValidationError(
                "Düzəliş növü, miqdarı, səbəbi və sənəd nömrəsi etibarsızdır."
            )
        reference_key = "adj:" + normalized_ref
        try:
            code = normalize_product_code(draft.product_code)
        except ValueError as error:
            raise InventoryValidationError("Məhsul kodu düzgün deyil.") from error
        delta = draft.quantity if draft.kind in {"RETURN", "CORRECTION"} else -draft.quantity
        try:
            async with self.session_factory.begin() as session:
                await self._authorize(session, actor, admin=True)
                product = await self._product(session, code)
                locked = await ProductRepository(session).get(product.id, for_update=True)
                if locked is None or not locked.is_active:
                    raise InventoryConflict("Məhsul artıq aktiv deyil.")
                await self._enforce_unit_policy(session, product.id, draft.unit, (location,))
                # Product-row lock fences all concurrent writes and adjustment
                # reference replays, even if the warehouse row is absent.
                duplicate = await session.scalar(
                    select(InventoryMovement.id).where(
                        InventoryMovement.product_id == product.id,
                        InventoryMovement.reference_key == reference_key,
                    )
                )
                if duplicate is not None:
                    raise InventoryConflict("Bu düzəliş sənədi artıq istifadə olunub.")
                balance = await session.scalar(
                    select(InventoryBalance)
                    .where(
                        InventoryBalance.product_id == product.id,
                        InventoryBalance.location_key == location,
                    )
                    .with_for_update()
                )
                if balance is None:
                    raise InventoryConflict("Bu anbarda təsdiqlənmiş açılış qalığı yoxdur.")
                before = balance.quantity
                after = before + delta
                if after < 0:
                    raise InventoryConflict("Anbarda kifayət qədər təsdiqlənmiş qalıq yoxdur.")
                reserved = await self._reserved_quantity(session, product.id, location)
                if after < reserved:
                    raise InventoryConflict("Düzəliş rezerv edilmiş stoku poza bilməz.")
                if after > _MAX_QUANTITY:
                    raise InventoryConflict("Düzəliş anbar qalıq limitini aşır.")
                balance.quantity = after
                movement = InventoryMovement(
                    product_id=product.id,
                    location_key=location,
                    location_name=balance.location_name,
                    kind="RECEIPT" if delta > 0 else "ISSUE",
                    quantity=abs(delta),
                    balance_after=after,
                    reference_key=reference_key,
                    reference_label=draft.reference.strip(),
                    approved_by_user_id=actor.id,
                )
                session.add(movement)
                await session.flush()
                session.add(
                    AuditEvent(
                        actor_user_id=actor.id,
                        action="INVENTORY_ADJUSTMENT_POSTED",
                        entity_type="inventory_movement",
                        entity_id=str(movement.id),
                        event_metadata={
                            "product_id": str(product.id),
                            "location_key": location,
                            "reference_key": reference_key,
                            "kind": draft.kind,
                            "reason": reason,
                            "delta": str(delta),
                        },
                    )
                )
                return StockAdjustmentReceipt(
                    product.display_code,
                    balance.location_name,
                    draft.kind,
                    before,
                    after,
                    delta,
                    draft.reference.strip(),
                )
        except IntegrityError as error:
            raise InventoryConflict("Təkrar düzəliş: heç nə iki dəfə yazılmadı.") from error

    @staticmethod
    def _reservation_key(reference: str) -> str:
        key = _key(reference)
        if not 1 <= len(key) <= 124 or any(ord(char) < 32 for char in key):
            raise InventoryValidationError("Rezervasiya sənəd nömrəsi düzgün deyil.")
        return "res:" + key

    async def reserve(self, actor: User, draft: StockReservationDraft) -> StockReservationReceipt:
        """Admin-only reservation of already verified available stock."""
        location = _key(draft.warehouse)
        scale = draft.quantity.as_tuple().exponent
        if (
            not 1 <= len(location) <= 128
            or not draft.quantity.is_finite()
            or not 0 < draft.quantity <= _MAX_QUANTITY
            or not isinstance(scale, int)
            or scale < -3
        ):
            raise InventoryValidationError("Rezervasiya anbarı və miqdarı düzgün deyil.")
        reference_key = self._reservation_key(draft.reference)
        code = normalize_product_code(draft.product_code)
        try:
            async with self.session_factory.begin() as session:
                await self._authorize(session, actor, admin=True)
                product = await self._product(session, code)
                locked = await ProductRepository(session).get(product.id, for_update=True)
                if locked is None or not locked.is_active:
                    raise InventoryConflict("Məhsul artıq aktiv deyil.")
                await self._enforce_unit_policy(session, product.id, draft.unit, (location,))
                previous = await session.scalar(
                    select(InventoryReservation.id).where(
                        InventoryReservation.product_id == product.id,
                        InventoryReservation.reference_key == reference_key,
                    )
                )
                if previous is not None:
                    raise InventoryConflict("Rezervasiya nömrəsi artıq istifadə olunub.")
                balance = await session.scalar(
                    select(InventoryBalance)
                    .where(
                        InventoryBalance.product_id == product.id,
                        InventoryBalance.location_key == location,
                    )
                    .with_for_update()
                )
                if balance is None:
                    raise InventoryConflict("Anbar açılış qalığı təsdiqlənməyib.")
                reserved = await self._reserved_quantity(session, product.id, location)
                if balance.quantity - reserved < draft.quantity:
                    raise InventoryConflict("Anbarda kifayət qədər sərbəst stok yoxdur.")
                item = InventoryReservation(
                    product_id=product.id,
                    location_key=location,
                    quantity=draft.quantity,
                    reference_key=reference_key,
                    reference_label=draft.reference.strip(),
                    status="ACTIVE",
                    approved_by_user_id=actor.id,
                )
                session.add(item)
                await session.flush()
                session.add(
                    AuditEvent(
                        actor_user_id=actor.id,
                        action="INVENTORY_RESERVATION_CREATED",
                        entity_type="inventory_reservation",
                        entity_id=str(item.id),
                        event_metadata={
                            "product_id": str(product.id),
                            "location_key": location,
                            "reference_key": reference_key,
                            "quantity": str(draft.quantity),
                        },
                    )
                )
                return StockReservationReceipt(
                    product.display_code,
                    balance.location_name,
                    draft.reference.strip(),
                    draft.quantity,
                    "ACTIVE",
                    balance.quantity,
                    reserved + draft.quantity,
                    balance.quantity - reserved - draft.quantity,
                )
        except IntegrityError as error:
            raise InventoryConflict("Rezervasiya təkrar edildi; heç nə dəyişmədi.") from error

    async def reservation_action(
        self, actor: User, *, product_code: str, reference: str, action: str
    ) -> StockReservationReceipt:
        """Cancel or consume one reservation, fenced by the same product lock.

        Consumption appends an ISSUE in the same transaction, so on-hand
        decreases precisely once. Cancellation never alters physical stock.
        """
        if action not in {"CANCEL", "CONSUME"}:
            raise InventoryValidationError("Rezervasiya əməliyyatı düzgün deyil.")
        reference_key = self._reservation_key(reference)
        code = normalize_product_code(product_code)
        try:
            async with self.session_factory.begin() as session:
                await self._authorize(session, actor, admin=True)
                product = await self._product(session, code)
                locked = await ProductRepository(session).get(product.id, for_update=True)
                if locked is None or not locked.is_active:
                    raise InventoryConflict("Məhsul artıq aktiv deyil.")
                reservation = await session.scalar(
                    select(InventoryReservation)
                    .where(
                        InventoryReservation.product_id == product.id,
                        InventoryReservation.reference_key == reference_key,
                    )
                    .with_for_update()
                )
                if reservation is None:
                    raise InventoryConflict("Rezervasiya tapılmadı.")
                location = reservation.location_key
                balance = await session.scalar(
                    select(InventoryBalance)
                    .where(
                        InventoryBalance.product_id == product.id,
                        InventoryBalance.location_key == location,
                    )
                    .with_for_update()
                )
                if balance is None:
                    raise InventoryConflict("Təsdiqlənmiş anbar qalığı tapılmadı.")
                reserved = await self._reserved_quantity(session, product.id, location)
                target_status = "CANCELLED" if action == "CANCEL" else "CONSUMED"
                if reservation.status == target_status:
                    return StockReservationReceipt(
                        product.display_code,
                        balance.location_name,
                        reservation.reference_label,
                        reservation.quantity,
                        target_status,
                        balance.quantity,
                        reserved,
                        balance.quantity - reserved,
                    )
                if reservation.status != "ACTIVE":
                    raise InventoryConflict("Rezervasiya artıq başqa əməliyyatla yekunlaşdırılıb.")
                if action == "CONSUME":
                    if balance.quantity - reservation.quantity < reserved - reservation.quantity:
                        raise InventoryConflict("Rezervasiya üçün stok yetərli deyil.")
                    balance.quantity -= reservation.quantity
                    movement = InventoryMovement(
                        product_id=product.id,
                        location_key=location,
                        location_name=balance.location_name,
                        kind="ISSUE",
                        quantity=reservation.quantity,
                        balance_after=balance.quantity,
                        reference_key=reference_key,
                        reference_label=reservation.reference_label,
                        approved_by_user_id=actor.id,
                    )
                    session.add(movement)
                    await session.flush()
                reservation.status = target_status
                new_reserved = reserved - reservation.quantity
                session.add(
                    AuditEvent(
                        actor_user_id=actor.id,
                        action=(
                            "INVENTORY_RESERVATION_CANCELLED"
                            if action == "CANCEL"
                            else "INVENTORY_RESERVATION_CONSUMED"
                        ),
                        entity_type="inventory_reservation",
                        entity_id=str(reservation.id),
                        event_metadata={
                            "product_id": str(product.id),
                            "location_key": location,
                            "reference_key": reference_key,
                        },
                    )
                )
                return StockReservationReceipt(
                    product.display_code,
                    balance.location_name,
                    reservation.reference_label,
                    reservation.quantity,
                    target_status,
                    balance.quantity,
                    new_reserved,
                    balance.quantity - new_reserved,
                )
        except IntegrityError as error:
            raise InventoryConflict("Rezervasiya təkrar edildi; heç nə dəyişmədi.") from error
