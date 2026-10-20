"""Safe, one-time administrator adjustment and reservation reviews."""

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from typing import TypedDict, cast
from unittest.mock import AsyncMock

import pytest

from office_assistant.application.inventory import (
    InventoryConflict,
    InventoryLedgerService,
    InventoryValidationError,
    StockAdjustmentReceipt,
    StockLocation,
    StockReservationReceipt,
    StockSnapshot,
)
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.miniapp.stock_actions import MiniAppStockActionReview


def actor(role: UserRole = UserRole.ADMIN, *, id: int = 4) -> User:
    return User(id=id, telegram_user_id=19, role=role, status=UserStatus.ACTIVE)


def mock_ledger() -> SimpleNamespace:
    return SimpleNamespace(
        snapshot=AsyncMock(
            return_value=StockSnapshot(
                "KA45", (StockLocation("Əsas", Decimal("9")),), Decimal("9"), "PCS"
            )
        ),
        adjust=AsyncMock(
            return_value=StockAdjustmentReceipt(
                "KA45", "Əsas", "DAMAGE", Decimal("9"), Decimal("8"), Decimal("-1"), "ADJ-01"
            )
        ),
        reserve=AsyncMock(
            return_value=StockReservationReceipt(
                "KA45",
                "Əsas",
                "RES-01",
                Decimal("2"),
                "ACTIVE",
                Decimal("9"),
                Decimal("2"),
                Decimal("7"),
            )
        ),
    )


class StockFields(TypedDict):
    kind: str
    code: str
    warehouse: str
    quantity: str
    reference: str
    unit: str | None
    reason: str | None


def fields(kind: str = "DAMAGE") -> StockFields:
    return {
        "kind": kind,
        "code": "KA45",
        "warehouse": "Əsas",
        "quantity": "1" if kind != "RESERVE" else "2",
        "reference": "ADJ-01" if kind != "RESERVE" else "RES-01",
        "unit": "PCS",
        "reason": None if kind == "RESERVE" else "Documented physical damage",
    }


def test_disabled_role_denied_and_no_preview_writes() -> None:
    async def scenario() -> None:
        ledger = mock_ledger()
        disabled = MiniAppStockActionReview(cast(InventoryLedgerService, ledger))
        with pytest.raises(PermissionError):
            await disabled.preview(actor(), **fields())
        review = MiniAppStockActionReview(cast(InventoryLedgerService, ledger), enabled=True)
        with pytest.raises(PermissionError):
            await review.preview(actor(UserRole.USER), **fields())
        preview = await review.preview(actor(), **fields())
        assert preview["on_hand_before"] == "9"
        assert preview["on_hand_after_if_approved"] == "8"
        assert ledger.adjust.await_count == ledger.reserve.await_count == 0

    asyncio.run(scenario())


def test_adjustment_ticket_is_single_use_and_actor_bound() -> None:
    async def scenario() -> None:
        ledger = mock_ledger()
        review = MiniAppStockActionReview(cast(InventoryLedgerService, ledger), enabled=True)
        preview = await review.preview(actor(), **fields())
        token = str(preview["token"])
        with pytest.raises(InventoryConflict):
            await review.confirm(actor(id=16), token=token)
        outcomes = await asyncio.gather(
            review.confirm(actor(), token=token),
            review.confirm(actor(), token=token),
            return_exceptions=True,
        )
        assert sum(isinstance(value, InventoryConflict) for value in outcomes) == 1
        assert sum(isinstance(value, dict) for value in outcomes) == 1
        assert ledger.adjust.await_count == 1
        assert ledger.reserve.await_count == 0

    asyncio.run(scenario())


def test_reservation_and_cancel_use_isolated_routes() -> None:
    async def scenario() -> None:
        ledger = mock_ledger()
        review = MiniAppStockActionReview(cast(InventoryLedgerService, ledger), enabled=True)
        preview = await review.preview(actor(), **fields("RESERVE"))
        result = await review.confirm(actor(), token=str(preview["token"]))
        assert result["status"] == "ACTIVE"
        assert result["available"] == "7"
        assert ledger.reserve.await_count == 1
        another = await review.preview(actor(), **fields("RESERVE"))
        assert await review.cancel(actor(), token=str(another["token"])) == {"status": "cancelled"}
        with pytest.raises(InventoryConflict):
            await review.confirm(actor(), token=str(another["token"]))
        assert ledger.reserve.await_count == 1

    asyncio.run(scenario())


def test_invalid_amount_reason_unit_and_missing_warehouse_fail_closed() -> None:
    async def scenario() -> None:
        ledger = mock_ledger()
        review = MiniAppStockActionReview(cast(InventoryLedgerService, ledger), enabled=True)
        invalid = (
            {"kind": "UNSUPPORTED"},
            {"quantity": "NaN"},
            {"quantity": "-2"},
            {"quantity": "0.0001"},
            {"quantity": "999999999999999999"},
            {"reason": "short"},
            {"unit": "BOX"},
            {"reference": "adj:reserved"},
        )
        for patch in invalid:
            with pytest.raises(InventoryValidationError):
                await review.preview(actor(), **cast(StockFields, fields() | patch))
        with pytest.raises(InventoryConflict):
            await review.preview(actor(), **cast(StockFields, fields() | {"warehouse": "Naməlum"}))
        assert ledger.adjust.await_count == ledger.reserve.await_count == 0

    asyncio.run(scenario())
