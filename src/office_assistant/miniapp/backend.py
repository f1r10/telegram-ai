"""Authenticated, read-only application boundary for the Telegram Mini App.

No writes, no client-supplied user IDs or business facts, and no external web
search. Business and document answers reuse the existing internal service.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.ai.service import InProcessRateGuard
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
        rate_limit_per_minute: int = 12,
        request_timeout_seconds: float = 75.0,
    ) -> None:
        if not bot_token or request_timeout_seconds <= 0:
            raise ValueError("Mini App requires server-side token and deadline")
        self.session_factory = session_factory
        self.bot_token = bot_token
        self.evidence = evidence
        self.inventory = inventory
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
