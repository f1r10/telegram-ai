"""Authenticated, read-only application boundary for the Telegram Mini App.

No writes, no client-supplied user IDs or business facts, and no external web
search. Business and document answers reuse the existing internal service.
"""

import asyncio
import logging
import re
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.ai.service import InProcessRateGuard
from office_assistant.application.sales_ledger import SalesLedgerService, SalesRequest
from office_assistant.business_tools.models import FindProductInput, ToolExecutionContext, ToolStatus
from office_assistant.business_tools.service import BusinessQueryService
from office_assistant.application.inventory import (
    InventoryLedgerService,
    parse_stock_request,
)
from office_assistant.infrastructure.database.models import User, UserStatus
from office_assistant.internal_evidence.service import InternalEvidenceService
from office_assistant.miniapp.auth import verify_telegram_init_data

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
        rate_limit_per_minute: int = 12,
        request_timeout_seconds: float = 75.0,
    ) -> None:
        if not bot_token or request_timeout_seconds <= 0:
            raise ValueError("Mini App requires server-side token and deadline")
        self.session_factory = session_factory
        self.bot_token = bot_token
        self.evidence = evidence
        self.inventory = inventory
        self.products = products
        self.sales = sales
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

    async def profile(self, init_data: str) -> dict[str, str]:
        actor = await self.actor(init_data)
        return {
            "display_name": actor.first_name or "Əməkdaş",
            "role": "admin" if actor.role == "ADMIN" else "employee",
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
        return ChatResponse(
            answer=result.answer[:16000],
            sources=tuple(
                (citation.number, citation.label[:240]) for citation in result.citations[:12]
            ),
            insufficient_evidence=result.insufficient_evidence,
        )

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
            "known": snapshot.total is not None,
            "total": str(snapshot.total) if isinstance(snapshot.total, Decimal) else None,
            "locations": [
                {"warehouse": x.warehouse, "quantity": str(x.quantity)} for x in snapshot.locations
            ],
            "notice": (
                "Yalnız təsdiqlənmiş anbar əməliyyatları əsasında."
                if snapshot.total is not None
                else "Açılış qalığı yoxdur: stok məlum deyil, sıfır hesab edilmir."
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

        def card(item: object) -> dict[str, object]:
            from office_assistant.business_tools.models import ProductView

            assert isinstance(item, ProductView)
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

    async def sales_report(
        self, init_data: str, *, customer: str, year: int
    ) -> dict[str, object]:
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
