"""Authoritative sales records: exact invoices, approved writes and deterministic totals."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.errors import AuthorizationDenied
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    SalesEntry,
    User,
    UserRole,
    UserStatus,
)

BAKU = ZoneInfo("Asia/Baku")
_SALE_PREFIX = re.compile(r"^\s*satış əlavə et\s*[:：]\s*(.*)$", re.I | re.S)
_REPORT_YEAR = re.compile(r"\b(20\d{2})\s*(?:-ci|-cı|-cu|-cü)?\s*il(?:də|de)?\b", re.I)
_SALES_WORDS = ("satış", "satmış", "satdig", "satdıq", "satilan", "dövriyyə")


@dataclass(frozen=True, slots=True)
class SaleDraft:
    customer_name: str
    sold_on: date
    invoice_number: str
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class SalesRequest:
    customer_name: str
    year: int


@dataclass(frozen=True, slots=True)
class SalesTotal:
    currency: str
    invoice_count: int
    amount: Decimal


class SaleValidationError(ValueError):
    pass


class SaleAlreadyExists(ValueError):
    pass


def customer_key(name: str) -> str:
    return " ".join(name.casefold().split())


def parse_sale_draft(text: str, *, today: date | None = None) -> SaleDraft | None:
    """Require invoice identity; casual chat must never create sales records."""

    prefix = _SALE_PREFIX.fullmatch(text)
    if prefix is None:
        return None
    parts = [part.strip() for part in prefix.group(1).split("|")]
    if len(parts) != 5:
        raise SaleValidationError(
            "Format: Satış əlavə et: Şirkət | YYYY-MM-DD | Faktura № | 125.50 | AZN"
        )
    customer, raw_date, invoice, raw_amount, raw_currency = parts
    if not 1 <= len(customer) <= 255 or not customer_key(customer):
        raise SaleValidationError("Şirkətin adını qeyd edin.")
    if not 1 <= len(invoice) <= 100:
        raise SaleValidationError("Təkrar olmayan faktura nömrəsi yazın.")
    try:
        sold_on = date.fromisoformat(raw_date)
    except ValueError as error:
        raise SaleValidationError("Tarixi YYYY-MM-DD formatında yazın.") from error
    if sold_on > (today or datetime.now(BAKU).date()):
        raise SaleValidationError("Gələcək tarixli əməliyyat tamamlanmış satış sayıla bilməz.")
    try:
        amount = Decimal(raw_amount)
    except InvalidOperation as error:
        raise SaleValidationError("Məbləğ düzgün deyil.") from error
    if not amount.is_finite() or amount <= 0 or amount > Decimal("9999999999999999.99"):
        raise SaleValidationError("Məbləğ müsbət və sistem limitləri daxilində olmalıdır.")
    if amount.as_tuple().exponent < -2:
        raise SaleValidationError("Məbləği ən çox iki onluq rəqəmlə yazın.")
    currency = raw_currency.upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise SaleValidationError("Valyutanı AZN, USD və ya digər üçhərfli kodla yazın.")
    return SaleDraft(customer, sold_on, invoice, amount, currency)


def parse_sales_request(text: str, *, today: date | None = None) -> SalesRequest | None:
    """Recognize bounded Azerbaijani yearly customer-sales questions and menu replies."""

    now = today or datetime.now(BAKU).date()
    raw = " ".join(text.strip().split())
    if re.match(r"(?i)^satış hesabatı\s*[:：]", raw):
        raw = re.sub(r"(?i)^satış hesabatı\s*[:：]\s*", "", raw)
    if "|" in raw or "/" in raw:
        parts = [p.strip() for p in re.split(r"[|/]", raw)]
        if len(parts) == 2 and re.fullmatch(r"20\d{2}", parts[1]):
            if 1 <= len(parts[0]) <= 255:
                return SalesRequest(parts[0], int(parts[1]))
        return None
    lowered = raw.casefold()
    if not any(word in lowered for word in _SALES_WORDS):
        return None
    if not any(word in lowered for word in ("nə qədər", "nəqədər", "ne qeder", "hesabat", "dövriyyə")):
        return None
    year_match = _REPORT_YEAR.search(raw)
    if year_match is not None:
        year = int(year_match.group(1))
    elif "bu il" in lowered:
        year = now.year
    else:
        return None
    # "bu il A şirkətinə nə qədər satmışıq" and
    # "2026-cı ildə A şirkətinə nə qədər satış etmişik" are canonical paths.
    candidate = re.sub(r"(?i)\bbu il\b", "", raw)
    candidate = _REPORT_YEAR.sub("", candidate)
    candidate = re.sub(r"(?i)^\s*(?:biz\s+)?", "", candidate)
    candidate = re.split(r"(?i)\s+nə\s*qədər\b|\s+ne qeder\b", candidate)[0]
    candidate = re.sub(r"(?i)\b(?:şirkətinə|şirketine)\b", "şirkəti", candidate)
    candidate = re.sub(r"(?i)\b(?:müştərisinə|musterisine)\b", "müştərisi", candidate)
    candidate = re.sub(r"(?i)^\s*(?:satış hesabatı|dövriyyə)\s*[:：]?\s*", "", candidate)
    candidate = candidate.strip(" .,?!")
    if not candidate or len(candidate) > 255:
        return None
    return SalesRequest(candidate, year)


class SalesLedgerService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    @staticmethod
    async def _require_actor(
        session: AsyncSession, actor: User, *, admin: bool = False
    ) -> None:
        db_user = await session.scalar(
            select(User).where(
                User.id == actor.id,
                User.telegram_user_id == actor.telegram_user_id,
                User.status == UserStatus.ACTIVE,
            )
        )
        if db_user is None:
            raise AuthorizationDenied("Inactive or missing sales ledger actor")
        if admin and db_user.role != UserRole.ADMIN:
            raise AuthorizationDenied("Only the company administrator can post sales")

    async def record(self, actor: User, draft: SaleDraft) -> SalesEntry:
        try:
            async with self.session_factory.begin() as session:
                await self._require_actor(session, actor, admin=True)
                entry = SalesEntry(
                    customer_name=draft.customer_name,
                    customer_key=customer_key(draft.customer_name),
                    sold_on=draft.sold_on,
                    invoice_number=draft.invoice_number,
                    amount=draft.amount,
                    currency=draft.currency,
                    status="POSTED",
                    created_by_user_id=actor.id,
                )
                session.add(entry)
                await session.flush()
                session.add(
                    AuditEvent(
                        actor_user_id=actor.id,
                        action="SALES_ENTRY_POSTED",
                        entity_type="sales_entry",
                        entity_id=str(entry.id),
                        event_metadata={
                            "invoice_number": draft.invoice_number,
                            "customer_key": customer_key(draft.customer_name),
                        },
                    )
                )
                return entry
        except IntegrityError as error:
            raise SaleAlreadyExists("Bu şirkət üçün faktura artıq qeydiyyatdadır.") from error

    async def totals(
        self, actor: User, request: SalesRequest
    ) -> tuple[SalesTotal, ...]:
        if not 2000 <= request.year <= 2099:
            raise SaleValidationError("Hesabat ili düzgün deyil.")
        start = date(request.year, 1, 1)
        end = date(request.year + 1, 1, 1)
        async with self.session_factory() as session:
            await self._require_actor(session, actor)
            rows = (
                await session.execute(
                    select(
                        SalesEntry.currency,
                        func.count(SalesEntry.id),
                        func.sum(SalesEntry.amount),
                    )
                    .where(
                        SalesEntry.customer_key == customer_key(request.customer_name),
                        SalesEntry.sold_on >= start,
                        SalesEntry.sold_on < end,
                        SalesEntry.status == "POSTED",
                    )
                    .group_by(SalesEntry.currency)
                    .order_by(SalesEntry.currency)
                )
            ).all()
        return tuple(
            SalesTotal(currency=str(currency), invoice_count=int(count), amount=Decimal(amount))
            for currency, count, amount in rows
        )
