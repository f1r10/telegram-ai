"""Isolated two-stage admin approval for stock adjustments and new reservations.

No free-form chat or file-ingestion write path. A preview is never a DB
write; on confirmation, the authoritative stock ledger rechecks all
permissions, reservations and physical balances in a database transaction.
"""

import asyncio
import secrets
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from time import monotonic

from office_assistant.application.inventory import (
    InventoryConflict,
    InventoryLedgerService,
    InventoryValidationError,
    StockAdjustmentDraft,
    StockReservationDraft,
)
from office_assistant.infrastructure.database.models import User, UserRole

KINDS = frozenset({"RETURN", "DAMAGE", "WRITE_OFF", "CORRECTION", "RESERVE"})
FINALIZATION_KINDS = {"RESERVATION_CANCEL": "CANCEL", "RESERVATION_CONSUME": "CONSUME"}
_TTL = 120
_MAX_TICKETS = 32
_MAX_PER_ACTOR = 3
_MAX_AMOUNT = Decimal("999999999999999.999")


@dataclass(frozen=True, slots=True)
class ReservationFinalizationDraft:
    product_code: str
    reference: str
    action: str


@dataclass(frozen=True, slots=True)
class ReviewTicket:
    actor_id: int
    draft: StockAdjustmentDraft | StockReservationDraft | ReservationFinalizationDraft
    expires_at: float


def _field(value: str, *, minimum: int = 1, maximum: int = 128) -> str:
    v = value.strip()
    if not minimum <= len(v) <= maximum or any(
        ord(ch) < 32 or ord(ch) == 127 or ch == "|" for ch in v
    ):
        raise InventoryValidationError("Təsdiq sahəsində etibarsız məlumat var.")
    return v


def _key(value: str) -> str:
    return " ".join(value.casefold().split())


def _amount(raw: str, *, signed: bool) -> Decimal:
    if not 1 <= len(raw) <= 24:
        raise InventoryValidationError("Miqdar limitini yoxlayın.")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError) as error:
        raise InventoryValidationError("Miqdar düzgün deyil.") from error
    scale = amount.as_tuple().exponent
    if (
        not amount.is_finite()
        or amount == 0
        or abs(amount) > _MAX_AMOUNT
        or (amount < 0 and not signed)
        or not isinstance(scale, int)
        or scale < -3
    ):
        raise InventoryValidationError("Miqdar düzgün deyil.")
    return amount


class MiniAppStockActionReview:
    def __init__(self, ledger: InventoryLedgerService, *, enabled: bool = False) -> None:
        self.ledger = ledger
        self.enabled = enabled
        self._tickets: dict[str, ReviewTicket] = {}
        self._lock = asyncio.Lock()

    def _admin(self, actor: User) -> None:
        if not self.enabled or actor.role != UserRole.ADMIN:
            raise PermissionError("This stock operation is not enabled for the actor")

    async def _issue_ticket(
        self,
        actor: User,
        draft: StockAdjustmentDraft | StockReservationDraft | ReservationFinalizationDraft,
    ) -> str:
        async with self._lock:
            now = monotonic()
            self._tickets = {
                token: item for token, item in self._tickets.items() if item.expires_at > now
            }
            if (
                len(self._tickets) >= _MAX_TICKETS
                or sum(item.actor_id == actor.id for item in self._tickets.values())
                >= _MAX_PER_ACTOR
            ):
                raise InventoryConflict("Çox sayda açıq təsdiq var.")
            token = secrets.token_urlsafe(24)
            self._tickets[token] = ReviewTicket(actor.id, draft, now + _TTL)
            return token

    async def preview(
        self,
        actor: User,
        *,
        kind: str,
        code: str,
        warehouse: str,
        quantity: str,
        reference: str,
        unit: str | None,
        reason: str | None,
    ) -> dict[str, object]:
        self._admin(actor)
        if kind in FINALIZATION_KINDS:
            if (
                warehouse.strip()
                or quantity.strip()
                or unit is not None
                or reason not in (None, "")
            ):
                raise InventoryValidationError(
                    "Rezervasiya yekunu üçün yalnız məhsul və rezervasiya nömrəsi yazılmalıdır."
                )
            code = _field(code, maximum=64)
            reference = _field(reference, maximum=124)
            details = await self.ledger.reservation_preview(
                actor, product_code=code, reference=reference
            )
            action = FINALIZATION_KINDS[kind]
            token = await self._issue_ticket(
                actor, ReservationFinalizationDraft(code, reference, action)
            )
            return {
                "token": token,
                "kind": kind,
                "product_code": details.product_code,
                "warehouse": details.warehouse,
                "quantity": str(details.quantity),
                "unit": details.unit_code,
                "reference": details.reference,
                "on_hand_before": str(details.on_hand),
                "on_hand_after_if_approved": str(
                    details.on_hand - details.quantity if action == "CONSUME" else details.on_hand
                ),
                "reserved_before": str(details.reserved),
                "available_before": str(details.available),
                "warning": (
                    "Bu yalnız aktiv rezervasiyanın önbaxışıdır. Cari status, səlahiyyət "
                    "və stok son təsdiqdə yenidən yoxlanılacaq."
                ),
            }
        if kind not in KINDS:
            raise InventoryValidationError("Əməliyyat növü düzgün deyil.")
        code = _field(code, maximum=64)
        warehouse = _field(warehouse)
        reference = _field(reference, maximum=124)
        if reference.casefold().startswith(("xfer:", "adj:", "res:")):
            raise InventoryValidationError("Sənəd nömrəsi ayrılmış prefiksdən başlamamalıdır.")
        value = _amount(quantity, signed=kind == "CORRECTION")
        unit = _field(unit, maximum=8).upper() if unit is not None else None
        if kind == "RESERVE":
            if reason not in (None, ""):
                raise InventoryValidationError("Rezervasiya üçün düzəliş səbəbi qəbul edilmir.")
            draft: StockAdjustmentDraft | StockReservationDraft = StockReservationDraft(
                code, warehouse, value, reference, unit
            )
        else:
            if reason is None:
                raise InventoryValidationError("Düzəlişin əsaslandırılması tələb olunur.")
            why = _field(reason, minimum=10, maximum=300)
            draft = StockAdjustmentDraft(code, warehouse, kind, value, reference, why, unit)

        snapshot = await self.ledger.snapshot(actor, code)
        location = next(
            (row for row in snapshot.locations if _key(row.warehouse) == _key(warehouse)),
            None,
        )
        if location is None:
            raise InventoryConflict("Anbar açılış qalığı təsdiqlənməyib.")
        if snapshot.unit_code != unit:
            raise InventoryValidationError(
                "Təsdiqlənmiş əsas vahid ilə əməliyyat vahidi eyni olmalıdır."
            )
        if kind in {"DAMAGE", "WRITE_OFF"}:
            delta = -value
        elif kind == "RESERVE":
            delta = Decimal(0)
        else:
            delta = value
        if location.quantity + delta < 0:
            raise InventoryConflict("Stok çıxışını qarşılamaq üçün təsdiqlənmiş qalıq yoxdur.")
        if location.quantity + delta > _MAX_AMOUNT:
            raise InventoryConflict("Stok maksimum qalıq limitini aşır.")
        # Preview never commits stock. Confirmation revalidates ACTIVE reservations.
        token = await self._issue_ticket(actor, draft)
        return {
            "token": token,
            "kind": kind,
            "product_code": snapshot.product_code,
            "warehouse": location.warehouse,
            "quantity": str(value),
            "unit": snapshot.unit_code,
            "reference": reference,
            "reason": reason,
            "on_hand_before": str(location.quantity),
            "on_hand_after_if_approved": str(location.quantity + delta),
            "warning": (
                "Önbaxış stok dəyişmir. Rezerv edilmiş stok və cari balans "
                "yalnız yekun təsdiq tranzaksiyasında yenidən yoxlanılır."
            ),
        }

    async def confirm(self, actor: User, *, token: str) -> dict[str, object]:
        self._admin(actor)
        if not 20 <= len(token) <= 96:
            raise InventoryValidationError("Təsdiq kodu yanlışdır.")
        async with self._lock:
            item = self._tickets.get(token)
            if item is None or item.actor_id != actor.id or item.expires_at <= monotonic():
                raise InventoryConflict(
                    "Təsdiq kodu etibarsız, vaxtı bitmiş və ya başqasına aiddir."
                )
            del self._tickets[token]  # One-use, including an ambiguous HTTP failure
        if isinstance(item.draft, ReservationFinalizationDraft):
            receipt = await self.ledger.reservation_action(
                actor,
                product_code=item.draft.product_code,
                reference=item.draft.reference,
                action=item.draft.action,
            )
            return {
                "status": receipt.status,
                "kind": "RESERVATION_" + item.draft.action,
                "product_code": receipt.product_code,
                "warehouse": receipt.warehouse,
                "reference": receipt.reference,
                "quantity": str(receipt.quantity),
                "on_hand": str(receipt.on_hand),
                "reserved": str(receipt.reserved),
                "available": str(receipt.available),
            }
        if isinstance(item.draft, StockReservationDraft):
            receipt = await self.ledger.reserve(actor, item.draft)
            return {
                "status": receipt.status,
                "kind": "RESERVE",
                "product_code": receipt.product_code,
                "warehouse": receipt.warehouse,
                "reference": receipt.reference,
                "quantity": str(receipt.quantity),
                "on_hand": str(receipt.on_hand),
                "reserved": str(receipt.reserved),
                "available": str(receipt.available),
            }
        receipt_adjust = await self.ledger.adjust(actor, item.draft)
        return {
            "status": "posted",
            "kind": receipt_adjust.kind,
            "product_code": receipt_adjust.product_code,
            "warehouse": receipt_adjust.warehouse,
            "reference": receipt_adjust.reference,
            "balance_before": str(receipt_adjust.balance_before),
            "balance_after": str(receipt_adjust.balance_after),
            "applied_delta": str(receipt_adjust.applied_delta),
        }

    async def cancel(self, actor: User, *, token: str) -> dict[str, object]:
        self._admin(actor)
        async with self._lock:
            item = self._tickets.get(token)
            if item is None or item.actor_id != actor.id:
                raise InventoryConflict("Təsdiq kodu etibarsız və ya başqasına aiddir.")
            del self._tickets[token]
        return {"status": "cancelled"}
