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

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.inventory import (
    InventoryLedgerService,
    StockLocation,
    StockSnapshot,
)
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
    return urlencode({
        **values,
        "hash": hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest(),
    })


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
) -> tuple[MiniAppBackend, Session]:
    user = actor if actor is not None else User(
        id=1, telegram_user_id=123, status=UserStatus.ACTIVE, role=UserRole.USER
    )
    factory = Sessions(user)
    backend = MiniAppBackend(
        cast(async_sessionmaker[AsyncSession], factory),
        bot_token=TOKEN,
        evidence=cast(InternalEvidenceService | None, evidence),
        inventory=cast(InventoryLedgerService | None, inventory),
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
                StockSnapshot(
                    "KA45", (StockLocation("Əsas", Decimal("0")),), Decimal("0")
                ),
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
    session.actor = User(
        id=1, telegram_user_id=123, role=UserRole.USER, status=UserStatus.DISABLED
    )
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
