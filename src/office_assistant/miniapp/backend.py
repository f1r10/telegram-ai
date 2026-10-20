"""Authenticated, read-only application boundary for the Telegram Mini App.

No writes, no client-supplied user IDs or business facts, and no external web
search. Business and document answers reuse the existing internal service.
"""

import asyncio
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.ai.service import InProcessRateGuard
from office_assistant.application.inventory import InventoryLedgerService, parse_stock_request
from office_assistant.application.sales_ledger import SalesLedgerService, SalesRequest
from office_assistant.business_tools.models import (
    FindProductInput,
    ProductView,
    ToolExecutionContext,
    ToolStatus,
)
from office_assistant.business_tools.service import BusinessQueryService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.internal_evidence.delivery import (
    DeliverableFile,
    FileDeliveryError,
    SourceFileDeliveryService,
)
from office_assistant.internal_evidence.service import InternalEvidenceService
from office_assistant.miniapp.auth import verify_telegram_init_data
from office_assistant.miniapp.photo_lookup import MiniAppPhotoLookup
from office_assistant.miniapp.progress import MiniAppDocumentProgress
from office_assistant.miniapp.stock_actions import MiniAppStockActionReview
from office_assistant.miniapp.stock_transfer import MiniAppStockTransferReview
from office_assistant.miniapp.stock_xlsx import MiniAppStockXlsxReview
from office_assistant.miniapp.uploads import MiniAppUploadWorkflow

logger = logging.getLogger(__name__)

_STOCK_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-/]{0,63}\Z")


class MiniAppAccessDenied(PermissionError):
    pass


class MiniAppRateLimited(RuntimeError):
    pass


class MiniAppServiceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ChatResponse:
    answer: str
    sources: tuple[tuple[int, str], ...]
    insufficient_evidence: bool
    source_actions: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True, slots=True)
class SourceTicket:
    actor_id: int
    telegram_user_id: int
    file_id: int
    citation_number: int
    expires_at: float


class MiniAppBackend:
    """Actor is resolved from signed launch data, then rechecked in PostgreSQL."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        bot_token: str,
        evidence: InternalEvidenceService | None = None,
        inventory: InventoryLedgerService | None = None,
        products: BusinessQueryService | None = None,
        sales: SalesLedgerService | None = None,
        file_delivery: SourceFileDeliveryService | None = None,
        uploads: MiniAppUploadWorkflow | None = None,
        progress: MiniAppDocumentProgress | None = None,
        photo_lookup: MiniAppPhotoLookup | None = None,
        stock_transfer_enabled: bool = False,
        stock_actions_enabled: bool = False,
        stock_xlsx_enabled: bool = False,
        rate_limit_per_minute: int = 12,
        request_timeout_seconds: float = 75.0,
    ) -> None:
        if not bot_token or request_timeout_seconds <= 0:
            raise ValueError("Mini App requires server-side token and deadline")
        self.session_factory = session_factory
        self.bot_token = bot_token
        self.evidence = evidence
        self.inventory = inventory
        self.stock_transfers = (
            MiniAppStockTransferReview(inventory, enabled=stock_transfer_enabled)
            if inventory is not None
            else None
        )
        self.stock_actions = (
            MiniAppStockActionReview(inventory, enabled=stock_actions_enabled)
            if inventory is not None
            else None
        )
        self.stock_xlsx = (
            MiniAppStockXlsxReview(inventory, enabled=stock_xlsx_enabled)
            if inventory is not None
            else None
        )
        self.products = products
        self.sales = sales
        self.file_delivery = file_delivery
        self.uploads = uploads
        self.progress = progress
        self.photo_lookup = photo_lookup
        self._source_tickets: dict[str, SourceTicket] = {}
        self.rate_guard = InProcessRateGuard(rate_limit_per_minute)
        self.request_timeout_seconds = request_timeout_seconds
        self._inflight = asyncio.Semaphore(4)

    async def actor(self, init_data: str) -> User:
        verified = verify_telegram_init_data(init_data, self.bot_token)
        async with self.session_factory() as session:
            actor = await session.scalar(
                select(User).where(
                    User.telegram_user_id == verified.telegram_user_id,
                    User.status == UserStatus.ACTIVE,
                )
            )
            if actor is None:
                raise MiniAppAccessDenied("Only approved active office accounts can use the app")
            return actor

    async def profile(self, init_data: str) -> dict[str, str | bool]:
        actor = await self.actor(init_data)
        return {
            "display_name": actor.first_name or "Əməkdaş",
            "role": "admin" if actor.role == "ADMIN" else "employee",
            "can_stock_xlsx": (
                self.stock_xlsx is not None
                and self.stock_xlsx.enabled
                and actor.role == UserRole.ADMIN
            ),
            "can_stock_actions": (
                self.stock_actions is not None
                and self.stock_actions.enabled
                and actor.role == UserRole.ADMIN
            ),
            "can_transfer": (
                self.stock_transfers is not None
                and self.stock_transfers.enabled
                and actor.role == UserRole.ADMIN
            ),
        }

    async def answer(self, init_data: str, *, question: str, scope: str) -> ChatResponse:
        actor = await self.actor(init_data)
        if scope not in {"chat", "baza"}:
            raise ValueError("Unsupported source scope")
        question = question.strip()
        if not 1 <= len(question) <= 2000:
            raise ValueError("Sual 1–2000 simvol arasında olmalıdır.")
        if self.evidence is None:
            raise MiniAppServiceUnavailable("Company knowledge is not configured")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        # The next answer supersedes any older one-tap source buttons for this actor.
        self._source_tickets = {
            token: ticket
            for token, ticket in self._source_tickets.items()
            if ticket.actor_id != actor.id and ticket.expires_at > monotonic()
        }
        if self._inflight.locked():
            raise MiniAppRateLimited("The assistant is busy")
        async with self._inflight:
            try:
                async with asyncio.timeout(self.request_timeout_seconds):
                    result = await self.evidence.answer(
                        actor,
                        question,
                        force_internal=(scope == "baza"),
                        image_readings=[],
                    )
            except TimeoutError as error:
                raise MiniAppServiceUnavailable("AI request timed out") from error
        source_actions: list[tuple[int, str]] = []
        if self.file_delivery is not None:
            for citation in result.citations[:12]:
                if len(source_actions) >= 3:
                    break
                if citation.file_id is None or not 1 <= citation.number <= 99:
                    continue
                token = secrets.token_urlsafe(24)
                self._source_tickets[token] = SourceTicket(
                    actor_id=actor.id,
                    telegram_user_id=actor.telegram_user_id,
                    file_id=citation.file_id,
                    citation_number=citation.number,
                    expires_at=monotonic() + 600,
                )
                source_actions.append((citation.number, token))
        # Memory budget: per-session tickets are ephemeral, bounded and never persisted.
        while len(self._source_tickets) > 256:
            self._source_tickets.pop(next(iter(self._source_tickets)))
        return ChatResponse(
            answer=result.answer[:16000],
            sources=tuple(
                (citation.number, citation.label[:240]) for citation in result.citations[:12]
            ),
            insufficient_evidence=result.insufficient_evidence,
            source_actions=tuple(source_actions),
        )

    async def source_file(self, init_data: str, *, token: str) -> DeliverableFile:
        """Resolve the original file through the latest persisted citation and actor."""

        actor = await self.actor(init_data)
        if self.file_delivery is None:
            raise MiniAppServiceUnavailable("Original file delivery is not configured")
        if not 20 <= len(token) <= 64:
            raise FileDeliveryError("Invalid source ticket")
        ticket = self._source_tickets.get(token)
        if (
            ticket is None
            or ticket.actor_id != actor.id
            or ticket.telegram_user_id != actor.telegram_user_id
            or monotonic() > ticket.expires_at
        ):
            raise FileDeliveryError("Source button is expired or belongs to another user")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        original = await self.file_delivery.resolve_recent(
            actor.telegram_user_id, ticket.citation_number
        )
        if original.file_id != ticket.file_id:
            raise FileDeliveryError("Original citation has been superseded")
        return original

    async def stock_xlsx_permission(self, init_data: str) -> None:
        actor = await self.actor(init_data)
        if actor.role != UserRole.ADMIN:
            raise MiniAppAccessDenied("Administrator required")
        if self.stock_xlsx is None or not self.stock_xlsx.enabled:
            raise MiniAppServiceUnavailable("XLSX stock approval is disabled")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")

    async def preview_stock_xlsx(self, init_data: str, *, payload: bytes) -> dict[str, object]:
        actor = await self.actor(init_data)
        if actor.role != UserRole.ADMIN:
            raise MiniAppAccessDenied("Administrator required")
        if self.stock_xlsx is None or not self.stock_xlsx.enabled:
            raise MiniAppServiceUnavailable("XLSX stock approval is disabled")
        return await self.stock_xlsx.preview(actor, payload)

    async def confirm_stock_xlsx(self, init_data: str, *, token: str) -> dict[str, object]:
        actor = await self.actor(init_data)
        if actor.role != UserRole.ADMIN:
            raise MiniAppAccessDenied("Administrator required")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        if self.stock_xlsx is None or not self.stock_xlsx.enabled:
            raise MiniAppServiceUnavailable("XLSX stock approval is disabled")
        return await self.stock_xlsx.confirm(actor, token)

    async def cancel_stock_xlsx(self, init_data: str, *, token: str) -> dict[str, object]:
        actor = await self.actor(init_data)
        if actor.role != UserRole.ADMIN:
            raise MiniAppAccessDenied("Administrator required")
        if self.stock_xlsx is None or not self.stock_xlsx.enabled:
            raise MiniAppServiceUnavailable("XLSX stock approval is disabled")
        return await self.stock_xlsx.cancel(actor, token)

    def _stock_action_review(self) -> MiniAppStockActionReview:
        if self.stock_actions is None or not self.stock_actions.enabled:
            raise MiniAppServiceUnavailable("Stock actions are disabled")
        return self.stock_actions

    async def preview_stock_action(
        self,
        init_data: str,
        *,
        kind: str,
        code: str,
        warehouse: str,
        quantity: str,
        reference: str,
        unit: str | None,
        reason: str | None,
    ) -> dict[str, object]:
        actor = await self.actor(init_data)
        if actor.role != UserRole.ADMIN:
            raise MiniAppAccessDenied("Administrator required")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        return await self._stock_action_review().preview(
            actor,
            kind=kind,
            code=code,
            warehouse=warehouse,
            quantity=quantity,
            reference=reference,
            unit=unit,
            reason=reason,
        )

    async def confirm_stock_action(self, init_data: str, *, token: str) -> dict[str, object]:
        actor = await self.actor(init_data)
        if actor.role != UserRole.ADMIN:
            raise MiniAppAccessDenied("Administrator required")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        return await self._stock_action_review().confirm(actor, token=token)

    async def cancel_stock_action(self, init_data: str, *, token: str) -> dict[str, object]:
        actor = await self.actor(init_data)
        if actor.role != UserRole.ADMIN:
            raise MiniAppAccessDenied("Administrator required")
        return await self._stock_action_review().cancel(actor, token=token)

    def _stock_review(self) -> MiniAppStockTransferReview:
        if self.stock_transfers is None or not self.stock_transfers.enabled:
            raise MiniAppServiceUnavailable("Stock approval is disabled")
        return self.stock_transfers

    async def prepare_stock_transfer(
        self,
        init_data: str,
        *,
        code: str,
        source: str,
        destination: str,
        quantity: str,
        reference: str,
        unit: str | None,
    ) -> dict[str, object]:
        actor = await self.actor(init_data)
        if actor.role != UserRole.ADMIN:
            raise MiniAppAccessDenied("Administrator required")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        return await self._stock_review().preview(
            actor,
            code=code,
            source=source,
            destination=destination,
            quantity=quantity,
            reference=reference,
            unit=unit,
        )

    async def confirm_stock_transfer(self, init_data: str, *, token: str) -> dict[str, object]:
        actor = await self.actor(init_data)
        if actor.role != UserRole.ADMIN:
            raise MiniAppAccessDenied("Administrator required")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        return await self._stock_review().confirm(actor, token=token)

    async def cancel_stock_transfer(self, init_data: str, *, token: str) -> dict[str, object]:
        actor = await self.actor(init_data)
        if actor.role != UserRole.ADMIN:
            raise MiniAppAccessDenied("Administrator required")
        return await self._stock_review().cancel(actor, token=token)

    async def stock(self, init_data: str, *, code: str) -> dict[str, object]:
        actor = await self.actor(init_data)
        if self.inventory is None:
            raise MiniAppServiceUnavailable("Warehouse ledger is not configured")
        if not _STOCK_CODE.fullmatch(code):
            raise ValueError("Məhsul kodunu düzgün yazın (məsələn, KA45).")
        normalized = parse_stock_request("Stok: " + code)
        if normalized is None:
            raise ValueError("Invalid stock product code")
        snapshot = await self.inventory.snapshot(actor, normalized)
        return {
            "product_code": snapshot.product_code,
            "known": bool(snapshot.locations),
            "unit": snapshot.unit_code,
            "total": str(snapshot.total) if isinstance(snapshot.total, Decimal) else None,
            "total_reason": snapshot.total_reason,
            "locations": [
                {"warehouse": x.warehouse, "quantity": str(x.quantity)} for x in snapshot.locations
            ],
            "notice": (
                "Açılış qalığı yoxdur: stok məlum deyil, sıfır hesab edilmir."
                if not snapshot.locations
                else (
                    "Anbar qalıqları ayrı-ayrılıqda məlumdur, amma təsdiqlənmiş "
                    "ümumi vahid olmadığı üçün cəmlənmir."
                    if snapshot.total_reason == "UNVERIFIED_UNIT"
                    else (
                        "Yalnız açılışı təsdiqlənmiş anbarlar üzrə cəmdir; "
                        "bütün şirkət stokunun tamlığını təsdiqləmir."
                    )
                )
            ),
        }

    async def stock_history(self, init_data: str, *, code: str) -> dict[str, object]:
        """Only previously approved stock movements, never inferred invoice quantities."""
        actor = await self.actor(init_data)
        if self.inventory is None:
            raise MiniAppServiceUnavailable("Warehouse ledger is not configured")
        if not _STOCK_CODE.fullmatch(code):
            raise ValueError("Məhsul kodu düzgün deyil.")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        normalized = parse_stock_request("Stok: " + code)
        if normalized is None:
            raise ValueError("Invalid stock product code")
        rows = await self.inventory.history(actor, normalized, limit=12)
        return {
            "product_code": normalized,
            "items": [
                {
                    "warehouse": row.warehouse,
                    "kind": row.kind,
                    "quantity": str(row.quantity),
                    "balance_after": str(row.balance_after),
                    "reference": row.document_reference,
                    "confirmed_at": row.confirmed_at.isoformat(),
                }
                for row in rows
            ],
            "notice": (
                "Yalnız təsdiqlənmiş anbar hərəkətləri; ən son 12 qeyd."
                if rows
                else "Bu məhsul üzrə təsdiqlənmiş anbar hərəkəti tapılmadı."
            ),
        }

    async def product(self, init_data: str, *, query: str) -> dict[str, object]:
        """Only exact, active and authorized company product identities are shown."""

        actor = await self.actor(init_data)
        query = query.strip()
        if not 1 <= len(query) <= 120 or any(ord(c) < 32 for c in query):
            raise ValueError("Məhsul adını və ya kodunu yazın.")
        if self.products is None:
            raise MiniAppServiceUnavailable("Product catalog is not configured")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        context = ToolExecutionContext(
            user_id=actor.id,
            role="ADMIN" if actor.role == "ADMIN" else "USER",
            request_id=f"miniapp-product-{uuid4().hex}",
        )
        result = await self.products.find_product(
            context, FindProductInput(code_or_alias=query, include_inactive=False)
        )

        def card(item: ProductView) -> dict[str, object]:
            return {
                "code": item.display_code,
                "name": item.name,
                "description": item.description,
            }

        if result.status == ToolStatus.NOT_FOUND:
            return {"status": "not_found", "product": None, "candidates": []}
        if result.status == ToolStatus.AMBIGUOUS and result.data is not None:
            return {
                "status": "ambiguous",
                "product": None,
                "candidates": [card(item) for item in result.data.candidates[:5]],
            }
        if result.status != ToolStatus.OK or result.data is None or result.data.product is None:
            raise MiniAppServiceUnavailable("Product catalog returned no confirmed identity")
        return {
            "status": "found",
            "product": card(result.data.product),
            "candidates": [],
        }

    async def sales_report(self, init_data: str, *, customer: str, year: int) -> dict[str, object]:
        """Read immutable posted invoice totals; never merge currencies or infer sales."""

        actor = await self.actor(init_data)
        customer = " ".join(customer.split())
        current_year = datetime.now(ZoneInfo("Asia/Baku")).year
        if (
            not 1 <= len(customer) <= 160
            or any(ord(c) < 32 for c in customer)
            or not 2000 <= year <= current_year
        ):
            raise ValueError("Müştəri adı və hesabat ilini yoxlayın.")
        if self.sales is None:
            raise MiniAppServiceUnavailable("Sales ledger is not configured")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        totals = await self.sales.totals(actor, SalesRequest(customer_name=customer, year=year))
        return {
            "customer": customer,
            "year": year,
            "has_records": bool(totals),
            "totals": [
                {
                    "currency": row.currency,
                    "invoice_count": row.invoice_count,
                    "amount": str(row.amount),
                }
                for row in totals
            ],
            "notice": (
                "Yalnız uçota alınmış təsdiqlənmiş fakturalar əsasında."
                if totals
                else "Bu müştəri və il üzrə təsdiqlənmiş satış qeydi tapılmadı; "
                "bu, sıfır satışın təsdiqi deyil."
            ),
        }

    async def upload_permission(self, init_data: str) -> None:
        actor = await self.actor(init_data)
        if self.uploads is None:
            raise MiniAppServiceUnavailable("Document upload is not configured")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        await self.uploads.permission(actor)

    async def register_upload(
        self,
        init_data: str,
        *,
        key: str,
        filename: str,
        mime: str,
        size: int,
    ) -> dict[str, object]:
        actor = await self.actor(init_data)
        if self.uploads is None:
            raise MiniAppServiceUnavailable("Document upload is not configured")
        return await self.uploads.register(actor, key=key, filename=filename, mime=mime, size=size)

    async def confirm_upload(self, init_data: str, *, token: str) -> dict[str, object]:
        actor = await self.actor(init_data)
        if self.uploads is None:
            raise MiniAppServiceUnavailable("Document upload is not configured")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        return await self.uploads.confirm(actor, token)

    async def cancel_upload(self, init_data: str, *, token: str) -> dict[str, object]:
        actor = await self.actor(init_data)
        if self.uploads is None:
            raise MiniAppServiceUnavailable("Document upload is not configured")
        return await self.uploads.cancel(actor, token)

    async def photo(self, init_data: str, *, image_bytes: bytes) -> dict[str, object]:
        """Signed, rate-limited photograph search; never stores or confirms pictures."""

        actor = await self.actor(init_data)
        if self.photo_lookup is None:
            raise MiniAppServiceUnavailable("Photo lookup is not configured")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        if self._inflight.locked():
            raise MiniAppRateLimited("The assistant is busy")
        async with self._inflight:
            result = await self.photo_lookup.lookup(image_bytes)
        return result.payload()

    async def recent_uploads(self, init_data: str) -> dict[str, object]:
        """Read only the authenticated actor's most recent durable jobs."""

        actor = await self.actor(init_data)
        if self.progress is None:
            raise MiniAppServiceUnavailable("Document progress is not configured")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        return {"jobs": await self.progress.recent(actor)}

    async def upload_status(self, init_data: str, *, job_id: int) -> dict[str, object]:
        actor = await self.actor(init_data)
        if self.progress is None:
            raise MiniAppServiceUnavailable("Document progress is not configured")
        if not self.rate_guard.allow(actor.id):
            raise MiniAppRateLimited("Too many requests")
        return await self.progress.one(actor, job_id=job_id)
