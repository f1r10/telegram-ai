"""Short-lived, explicit administrator review for warehouse transfers.

No LLM/tool intent may call this workflow. The default feature flag is OFF.
Every commit consumes exactly one actor-bound ticket; the inventory ledger
rechecks current authorization, stock and reservations in its own transaction.
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
    StockTransferDraft,
)
from office_assistant.infrastructure.database.models import User, UserRole

_TICKET_TTL_SECONDS = 120
_MAX_PENDING = 32
_MAX_QUANTITY_TEXT = 24


@dataclass(frozen=True, slots=True)
class TransferTicket:
    actor_id: int
    draft: StockTransferDraft
    expires_at: float


def _location_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _text(value: str, *, limit: int = 128) -> str:
    result = value.strip()
    if (
        not 1 <= len(result) <= limit
        or any(ord(char) < 32 or ord(char) == 127 for char in result)
    ):
        raise InventoryValidationError("Anbar və ya sənəd məlumatı düzgün deyil.")
    return result


class MiniAppStockTransferReview:
    def __init__(self, ledger: InventoryLedgerService, *, enabled: bool = False) -> None:
        self.ledger = ledger
        self.enabled = enabled
        self._pending: dict[str, TransferTicket] = {}
        self._lock = asyncio.Lock()

    def _require_admin(self, actor: User) -> None:
        if not self.enabled:
            raise PermissionError("Stock writes are disabled on this deployment")
        if actor.role != UserRole.ADMIN:
            raise PermissionError("Administrator approval is required")

    async def preview(
        self,
        actor: User,
        *,
        code: str,
        source: str,
        destination: str,
        quantity: str,
        reference: str,
        unit: str | None,
    ) -> dict[str, object]:
        self._require_admin(actor)
        code = _text(code, limit=64)
        source = _text(source)
        destination = _text(destination)
        reference = _text(reference)
        if _location_key(source) == _location_key(destination):
            raise InventoryValidationError("Mənbə və təyinat anbarları fərqli olmalıdır.")
        if len(quantity) > _MAX_QUANTITY_TEXT:
            raise InventoryValidationError("Miqdar düzgün deyil.")
        try:
            value = Decimal(quantity)
        except (InvalidOperation, ValueError) as error:
            raise InventoryValidationError("Miqdar düzgün deyil.") from error
        scale = value.as_tuple().exponent
        if (
            not value.is_finite()
            or value <= 0
            or value > Decimal("999999999999999.999")
            or not isinstance(scale, int)
            or scale < -3
        ):
            raise InventoryValidationError("Miqdar düzgün deyil.")
        if unit is not None:
            unit = _text(unit, limit=8).upper()
        draft = StockTransferDraft(code, source, destination, value, reference, unit)
        snapshot = await self.ledger.snapshot(actor, code)
        by_warehouse = {_location_key(row.warehouse): row for row in snapshot.locations}
        before_source = by_warehouse.get(_location_key(source))
        before_destination = by_warehouse.get(_location_key(destination))
        if before_source is None or before_destination is None:
            raise InventoryConflict("Hər iki anbarın təsdiqlənmiş açılış qalığı tələb olunur.")
        if snapshot.unit_code is not None and unit != snapshot.unit_code:
            raise InventoryValidationError("Təsdiqlənmiş əsas ölçü vahidi tələb olunur.")
        if snapshot.unit_code is None and unit is not None:
            raise InventoryValidationError("Məhsul üçün ölçü vahidi təsdiqlənməyib.")
        if before_source.quantity < value:
            raise InventoryConflict("Mənbə anbarda kifayət qədər qalıq yoxdur.")
        # This is a provisional preview only: reservations and concurrent stock
        # changes are checked again, authoritatively, inside ledger.transfer.
        async with self._lock:
            now = monotonic()
            self._pending = {
                key: item for key, item in self._pending.items() if item.expires_at > now
            }
            if len(self._pending) >= _MAX_PENDING:
                raise InventoryConflict("Çox sayda açıq təsdiq var; bir qismini ləğv edin.")
            ticket = secrets.token_urlsafe(24)
            self._pending[ticket] = TransferTicket(actor.id, draft, now + _TICKET_TTL_SECONDS)
        return {
            "token": ticket,
            "product_code": snapshot.product_code,
            "source": before_source.warehouse,
            "destination": before_destination.warehouse,
            "quantity": str(value),
            "unit": snapshot.unit_code,
            "reference": reference,
            "source_after_preview": str(before_source.quantity - value),
            "destination_after_preview": str(before_destination.quantity + value),
            "warning": (
                "Bu yalnız önbaxışdır. Təsdiq zamanı stok, rezervasiya və icazələr "
                "yenidən yoxlanacaq; bu addım heç nəyi dəyişmir."
            ),
        }

    async def confirm(self, actor: User, *, token: str) -> dict[str, object]:
        self._require_admin(actor)
        if not 20 <= len(token) <= 96:
            raise InventoryValidationError("Təsdiq kodu düzgün deyil.")
        async with self._lock:
            item = self._pending.get(token)
            if item is None or item.actor_id != actor.id or item.expires_at <= monotonic():
                raise InventoryConflict("Təsdiq vaxtı bitib və ya sizə aid deyil.")
            del self._pending[token]  # atomic consume; never retry an ambiguous DB result
        result = await self.ledger.transfer(actor, item.draft)
        return {
            "status": "posted",
            "product_code": result.product_code,
            "source": result.source_warehouse,
            "destination": result.destination_warehouse,
            "quantity": str(result.quantity),
            "source_balance_after": str(result.source_balance_after),
            "destination_balance_after": str(result.destination_balance_after),
            "reference": result.reference,
        }

    async def cancel(self, actor: User, *, token: str) -> dict[str, object]:
        self._require_admin(actor)
        async with self._lock:
            item = self._pending.get(token)
            if item is None or item.actor_id != actor.id:
                raise InventoryConflict("Bu təsdiq sizə aid deyil və ya artıq bitib.")
            del self._pending[token]
        return {"status": "cancelled"}
