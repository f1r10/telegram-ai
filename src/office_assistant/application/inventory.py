"""Explicit, approval-gated inventory ledger; no catalog evidence implies stock.

A missing opening balance means UNKNOWN, never zero. Sale invoices, quotes,
payments, file uploads and model responses never post stock automatically.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.errors import AuthorizationDenied
from office_assistant.business.normalization import normalize_product_alias, normalize_product_code
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    InventoryBalance,
    InventoryMovement,
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


@dataclass(frozen=True, slots=True)
class StockTransferDraft:
    product_code: str
    source_warehouse: str
    destination_warehouse: str
    quantity: Decimal
    reference: str


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
class StockEntry:
    product_code: str
    warehouse: str
    kind: str
    quantity: Decimal
    balance_after: Decimal
    reference: str


@dataclass(frozen=True, slots=True)
class StockLocation:
    warehouse: str
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class StockSnapshot:
    product_code: str
    locations: tuple[StockLocation, ...]
    total: Decimal | None


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

    async def snapshot(self, actor: User, code: str) -> StockSnapshot:
        async with self.session_factory() as session:
            await self._authorize(session, actor)
            product = await self._product(session, normalize_product_code(code))
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
        if reference_key.startswith("xfer:"):
            raise InventoryValidationError("Köçürmə sənəd nömrəsi anbar giriş/çıxışında işlədilə bilməz.")
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
            raise InventoryValidationError("Köçürmə miqdarı, anbarlar və sənəd nömrəsi düzgün deyil.")
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
                    raise InventoryConflict(
                        "Hər iki anbarda təsdiqlənmiş açılış qalığı olmalıdır."
                    )
                prior = await session.scalar(
                    select(InventoryMovement.id).where(
                        InventoryMovement.product_id == product.id,
                        InventoryMovement.reference_key == reference_key,
                        InventoryMovement.location_key.in_([source_key, dest_key]),
                    )
                )
                if prior is not None:
                    raise InventoryConflict("Bu köçürmə sənədi artıq istifadə olunub.")
                if source.quantity < draft.quantity:
                    raise InventoryConflict("Mənbə anbarda kifayət qədər qalıq yoxdur.")
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
