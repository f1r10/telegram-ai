"""Bounded, single-use, administrator-confirmed XLSX inventory imports."""

import asyncio
import secrets
from dataclasses import dataclass
from time import monotonic

from office_assistant.application.inventory import (
    InventoryConflict,
    InventoryLedgerService,
    InventoryValidationError,
)
from office_assistant.application.inventory_xlsx import (
    MAX_XLSX_BYTES,
    StockXlsxPreview,
    approved_drafts,
    preview_stock_xlsx,
)
from office_assistant.infrastructure.database.models import User, UserRole

_TTL_SECONDS = 120
_MAX_PREVIEWS = 3


@dataclass(frozen=True, slots=True)
class WorkbookTicket:
    actor_id: int
    payload: bytes
    preview: StockXlsxPreview
    expires_at: float


class MiniAppStockXlsxReview:
    """The workbook stays in bounded private process memory, never chat history."""

    def __init__(self, ledger: InventoryLedgerService, *, enabled: bool = False) -> None:
        self.ledger = ledger
        self.enabled = enabled
        self._tickets: dict[str, WorkbookTicket] = {}
        self._lock = asyncio.Lock()

    def _admin(self, actor: User) -> None:
        if not self.enabled or actor.role != UserRole.ADMIN:
            raise PermissionError("Stock workbook approval is not available")

    async def preview(self, actor: User, payload: bytes) -> dict[str, object]:
        self._admin(actor)
        if not 0 < len(payload) <= MAX_XLSX_BYTES:
            raise InventoryValidationError("Excel həcmi limitə uyğun deyil.")
        summary = await asyncio.to_thread(preview_stock_xlsx, payload)
        async with self._lock:
            now = monotonic()
            self._tickets = {
                token: item for token, item in self._tickets.items() if item.expires_at > now
            }
            if (
                len(self._tickets) >= _MAX_PREVIEWS
                or any(ticket.actor_id == actor.id for ticket in self._tickets.values())
            ):
                raise InventoryConflict("Əvvəlki Excel önbaxışını təsdiqləyin və ya ləğv edin.")
            token = secrets.token_urlsafe(24)
            self._tickets[token] = WorkbookTicket(
                actor.id, payload, summary, now + _TTL_SECONDS
            )
        return {
            "token": token,
            **summary.safe_summary(),
            "items": [
                {
                    "code": draft.product_code,
                    "warehouse": draft.warehouse,
                    "kind": draft.kind,
                    "quantity": str(draft.quantity),
                    "unit": draft.unit,
                    "reference": draft.reference,
                }
                for draft in summary.drafts
            ],
            "notice": (
                "Hamısını yoxlayın: bu önbaxış stok yazmır. Yekun təsdiqdə "
                "sənədin eyni SHA-256 məzmunu və stok yenidən yoxlanılacaq."
            ),
        }

    async def confirm(self, actor: User, token: str) -> dict[str, object]:
        self._admin(actor)
        if not 20 <= len(token) <= 96:
            raise InventoryValidationError("Excel təsdiq kodu yanlışdır.")
        async with self._lock:
            ticket = self._tickets.get(token)
            if ticket is None or ticket.actor_id != actor.id or ticket.expires_at <= monotonic():
                raise InventoryConflict("Excel təsdiqi etibarsızdır və ya vaxtı bitib.")
            del self._tickets[token]  # One-use even if the outcome is ambiguous
        drafts = await asyncio.to_thread(
            approved_drafts, ticket.payload, confirmed_sha256=ticket.preview.sha256
        )
        receipts = await self.ledger.record_batch(
            actor, drafts, workbook_sha256=ticket.preview.sha256
        )
        return {
            "status": "posted",
            "sha256": ticket.preview.sha256,
            "count": len(receipts),
            "references": [item.reference for item in receipts],
        }

    async def cancel(self, actor: User, token: str) -> dict[str, object]:
        self._admin(actor)
        async with self._lock:
            ticket = self._tickets.get(token)
            if ticket is None or ticket.actor_id != actor.id:
                raise InventoryConflict("Excel təsdiqi sizə aid deyil və ya artıq bitib.")
            del self._tickets[token]
        return {"status": "cancelled"}
