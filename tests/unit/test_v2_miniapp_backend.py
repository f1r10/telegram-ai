"""A verified Telegram user MUST still be active in the office database."""

import asyncio
import hashlib
import hmac
import json
import time
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.inventory import (
    InventoryLedgerService,
    StockLocation,
    StockSnapshot,
)
from office_assistant.application.sales_ledger import SalesLedgerService, SalesTotal
from office_assistant.business_tools.models import (
    ProductLookupView,
    ProductView,
    ToolResult,
    ToolStatus,
)
from office_assistant.business_tools.service import BusinessQueryService
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.internal_evidence.models import (
    InternalAnswer,
    RoutingCategory,
    UnifiedCitation,
)
from office_assistant.internal_evidence.service import InternalEvidenceService
from office_assistant.miniapp.auth import MiniAppAuthenticationError
from office_assistant.miniapp.backend import (
    MiniAppAccessDenied,
    MiniAppBackend,
    MiniAppServiceUnavailable,
)

TOKEN = "123456:TEST-only"


def signature(user_id: int) -> str:
    values = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "Employee"}),
    }
    canonical = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    return urlencode(
        {
            **values,
            "hash": hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest(),
        }
    )


class Session:
    def __init__(self, actor: User | None) -> None:
        self.actor = actor
        self.executions = 0

    async def __aenter__(self) -> "Session":
        return self

    async def __aexit__(self, *_args: object) -> None:
        pass

    async def scalar(self, _query: object) -> User | None:
        self.executions += 1
        if self.actor is None or self.actor.status != UserStatus.ACTIVE:
            return None
        return self.actor


class Sessions:
    def __init__(self, actor: User | None) -> None:
        self.session = Session(actor)

    def __call__(self) -> Session:
        return self.session


def make_backend(
    *,
    actor: User | None = None,
    evidence: object | None = None,
    inventory: object | None = None,
    products: object | None = None,
    sales: object | None = None,
) -> tuple[MiniAppBackend, Session]:
    user = (
        actor
        if actor is not None
        else User(id=1, telegram_user_id=123, status=UserStatus.ACTIVE, role=UserRole.USER)
    )
    factory = Sessions(user)
    backend = MiniAppBackend(
        cast(async_sessionmaker[AsyncSession], factory),
        bot_token=TOKEN,
        evidence=cast(InternalEvidenceService | None, evidence),
        inventory=cast(InventoryLedgerService | None, inventory),
        products=cast(BusinessQueryService | None, products),
        sales=cast(SalesLedgerService | None, sales),
    )
    return backend, factory.session


def test_signed_user_profile_and_db_approval_are_distinct() -> None:
    backend, session = make_backend()
    assert asyncio.run(backend.profile(signature(123))) == {
        "display_name": "Əməkdaş",
        "role": "employee",
    }
    assert session.executions == 1
    with pytest.raises(MiniAppAuthenticationError):
        asyncio.run(backend.profile(signature(999) + "&user=bad"))
    session.actor = User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.DISABLED)
    with pytest.raises(MiniAppAccessDenied):
        asyncio.run(backend.profile(signature(123)))


def test_chat_and_company_base_reuse_existing_grounded_service() -> None:
    evidence = SimpleNamespace(
        answer=AsyncMock(
            return_value=InternalAnswer(
                answer="Təsdiqlənmiş daxili cavab.",
                route=RoutingCategory.INTERNAL_DOCUMENT,
                citations=(
                    UnifiedCitation(
                        evidence_id="file:1",
                        number=1,
                        source_class="INTERNAL_DOCUMENT",
                        file_id=9,
                        filename="contract.pdf",
                        label="Sənəd: contract.pdf",
                    ),
                ),
            )
        )
    )
    backend, session = make_backend(evidence=evidence)
    chat = asyncio.run(backend.answer(signature(123), question="Salam", scope="chat"))
    assert chat.sources == ((1, "Sənəd: contract.pdf"),)
    evidence.answer.assert_awaited_once_with(
        session.actor, "Salam", force_internal=False, image_readings=[]
    )
    asyncio.run(backend.answer(signature(123), question="KA45?", scope="baza"))
    assert evidence.answer.await_args.kwargs["force_internal"] is True
    with pytest.raises(ValueError):
        asyncio.run(backend.answer(signature(123), question="Q", scope="internet"))
    assert evidence.answer.await_count == 2


def test_warehouse_unknown_is_distinct_from_zero_and_reuses_stock_service() -> None:
    inventory = SimpleNamespace(
        snapshot=AsyncMock(
            side_effect=[
                StockSnapshot("KA45", (), None),
                StockSnapshot("KA45", (StockLocation("Əsas", Decimal("0")),), Decimal("0")),
            ]
        )
    )
    backend, session = make_backend(inventory=inventory)
    unknown = asyncio.run(backend.stock(signature(123), code="KA45"))
    assert unknown["known"] is False
    assert unknown["total"] is None
    known_zero = asyncio.run(backend.stock(signature(123), code="KA45"))
    assert known_zero["known"] is True
    assert known_zero["total"] == "0"
    inventory.snapshot.assert_awaited_with(session.actor, "KA45")


def test_unauthorized_user_cannot_query_business_data() -> None:
    backend, session = make_backend(evidence=SimpleNamespace(answer=AsyncMock()))
    session.actor = User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.DISABLED)
    with pytest.raises(MiniAppAccessDenied):
        asyncio.run(backend.answer(signature(123), question="Price of KA45", scope="chat"))
    with pytest.raises(MiniAppAccessDenied):
        asyncio.run(backend.stock(signature(123), code="KA45"))


def test_missing_ai_rejects_open_loop_model_invention() -> None:
    backend, _ = make_backend()
    with pytest.raises(MiniAppServiceUnavailable):
        asyncio.run(backend.answer(signature(123), question="Answer", scope="baza"))
    with pytest.raises(ValueError):
        asyncio.run(backend.answer(signature(123), question=" " * 4, scope="chat"))


def test_product_card_only_shows_authoritative_exact_match() -> None:
    product = ProductView(
        product_id=uuid4(),
        display_code="KA45",
        canonical_code="KA45",
        name="Təsdiqli məhsul",
        description="Daxili kataloq təsviri",
        is_active=True,
        matched_by="CANONICAL_CODE",
    )
    lookup = SimpleNamespace(
        find_product=AsyncMock(
            return_value=ToolResult[ProductLookupView](
                status=ToolStatus.OK,
                data=ProductLookupView(product=product),
            )
        )
    )
    backend, actor_session = make_backend(products=lookup)
    result = asyncio.run(backend.product(signature(123), query="  KA45  "))
    assert result == {
        "status": "found",
        "product": {
            "code": "KA45",
            "name": "Təsdiqli məhsul",
            "description": "Daxili kataloq təsviri",
        },
        "candidates": [],
    }
    context, request = lookup.find_product.await_args.args
    assert actor_session.actor is not None
    assert context.user_id == actor_session.actor.id
    assert request.code_or_alias == "KA45"
    assert request.include_inactive is False
    assert "price" not in result
    assert "stock" not in result


def test_product_ambiguity_does_not_invent_one_best_item() -> None:
    products = [
        ProductView(
            product_id=uuid4(),
            display_code=code,
            canonical_code=code,
            name="Oxşar",
            description=None,
            is_active=True,
            matched_by="ALIAS",
        )
        for code in ("KA45", "KA46")
    ]
    lookup = SimpleNamespace(
        find_product=AsyncMock(
            return_value=ToolResult[ProductLookupView](
                status=ToolStatus.AMBIGUOUS,
                data=ProductLookupView(candidates=products),
            )
        )
    )
    backend, _ = make_backend(products=lookup)
    result = asyncio.run(backend.product(signature(123), query="Oxşar"))
    assert result["status"] == "ambiguous"
    assert result["product"] is None
    assert isinstance(result["candidates"], list)
    assert len(result["candidates"]) == 2
    lookup.find_product.return_value = ToolResult[ProductLookupView](status=ToolStatus.NOT_FOUND)
    assert asyncio.run(backend.product(signature(123), query="Unknown"))["status"] == "not_found"


def test_sales_card_uses_posted_invoice_totals_without_currency_conversion() -> None:
    sales = SimpleNamespace(
        totals=AsyncMock(
            side_effect=[
                (
                    SalesTotal("AZN", 3, Decimal("135.50")),
                    SalesTotal("USD", 1, Decimal("20.00")),
                ),
                (),
            ]
        )
    )
    backend, actor_session = make_backend(sales=sales)
    result = asyncio.run(backend.sales_report(signature(123), customer="  A   şirkəti ", year=2026))
    assert result["customer"] == "A şirkəti"
    assert result["year"] == 2026
    assert result["totals"] == [
        {"currency": "AZN", "invoice_count": 3, "amount": "135.50"},
        {"currency": "USD", "invoice_count": 1, "amount": "20.00"},
    ]
    user, request = sales.totals.await_args_list[0].args
    assert user is actor_session.actor
    assert request.customer_name == "A şirkəti"
    assert request.year == 2026
    assert (
        asyncio.run(backend.sales_report(signature(123), customer="A şirkəti", year=2026))[
            "has_records"
        ]
        is False
    )


def test_product_sales_invalid_requests_and_revocation_never_run_queries() -> None:
    lookup = SimpleNamespace(find_product=AsyncMock())
    sales = SimpleNamespace(totals=AsyncMock())
    backend, session = make_backend(products=lookup, sales=sales)
    with pytest.raises(ValueError):
        asyncio.run(backend.product(signature(123), query=" " * 5))
    with pytest.raises(ValueError):
        asyncio.run(backend.sales_report(signature(123), customer="A", year=2099))
    assert lookup.find_product.await_count == 0
    assert sales.totals.await_count == 0
    session.actor = User(id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.DISABLED)
    with pytest.raises(MiniAppAccessDenied):
        asyncio.run(backend.product(signature(123), query="KA45"))
    with pytest.raises(MiniAppAccessDenied):
        asyncio.run(backend.sales_report(signature(123), customer="A", year=2026))
    lookup.find_product.assert_not_awaited()
    sales.totals.assert_not_awaited()
