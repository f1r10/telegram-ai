"""Approved stock transfers require a current admin, one-time ticket and DB recheck."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from office_assistant.application.inventory import (
    InventoryConflict,
    InventoryValidationError,
    StockLocation,
    StockSnapshot,
    StockTransferReceipt,
)
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.miniapp.stock_transfer import MiniAppStockTransferReview


def actor(role: UserRole = UserRole.ADMIN, *, id: int = 12) -> User:
    return User(id=id, telegram_user_id=123, role=role, status=UserStatus.ACTIVE)


def inventory() -> SimpleNamespace:
    return SimpleNamespace(
        snapshot=AsyncMock(
            return_value=StockSnapshot(
                "KA45",
                (StockLocation("Əsas", Decimal("9")), StockLocation("Kənar", Decimal("2"))),
                Decimal("11"),
                "PCS",
            )
        ),
        transfer=AsyncMock(
            return_value=StockTransferReceipt(
                "KA45", "Əsas", "Kənar", Decimal("6"), Decimal("5"),
                Decimal("3"), "MOVE-007",
            )
        ),
    )


def params() -> dict[str, str]:
    return {
        "code": "KA45",
        "source": "Əsas",
        "destination": "Kənar",
        "quantity": "3",
        "reference": "MOVE-007",
        "unit": "PCS",
    }


def test_transfer_is_disabled_by_default_and_not_available_to_employees() -> None:
    async def scenario() -> None:
        fake = inventory()
        disabled = MiniAppStockTransferReview(fake)
        with pytest.raises(PermissionError):
            await disabled.preview(actor(), **params())
        enabled = MiniAppStockTransferReview(fake, enabled=True)
        with pytest.raises(PermissionError):
            await enabled.preview(actor(UserRole.USER), **params())
        assert fake.snapshot.await_count == 0
        assert fake.transfer.await_count == 0

    asyncio.run(scenario())


def test_preview_does_not_write_and_ticket_posts_only_once_for_its_actor() -> None:
    async def scenario() -> None:
        fake = inventory()
        review = MiniAppStockTransferReview(fake, enabled=True)
        summary = await review.preview(actor(), **params())
        assert summary["source_after_preview"] == "6"
        assert summary["destination_after_preview"] == "5"
        assert fake.transfer.await_count == 0
        token = str(summary["token"])
        with pytest.raises(InventoryConflict):
            await review.confirm(actor(id=13), token=token)
        results = await asyncio.gather(
            review.confirm(actor(), token=token),
            review.confirm(actor(), token=token),
            return_exceptions=True,
        )
        assert sum(isinstance(value, InventoryConflict) for value in results) == 1
        assert sum(isinstance(value, dict) for value in results) == 1
        assert fake.transfer.await_count == 1
        assert results[0] != results[1]

    asyncio.run(scenario())


def test_cancel_expiry_and_conflict_require_fresh_preview() -> None:
    async def scenario() -> None:
        fake = inventory()
        review = MiniAppStockTransferReview(fake, enabled=True)
        preview = await review.preview(actor(), **params())
        token = str(preview["token"])
        with pytest.raises(InventoryConflict):
            await review.cancel(actor(id=500), token=token)
        assert await review.cancel(actor(), token=token) == {"status": "cancelled"}
        with pytest.raises(InventoryConflict):
            await review.confirm(actor(), token=token)
        new = await review.preview(actor(), **params())
        new_token = str(new["token"])
        review._pending[new_token] = replace(review._pending[new_token], expires_at=monotonic() - 1)
        with pytest.raises(InventoryConflict):
            await review.confirm(actor(), token=new_token)
        fake.transfer.side_effect = InventoryConflict("Reservation changed")
        latest = await review.preview(actor(), **params())
        latest_token = str(latest["token"])
        with pytest.raises(InventoryConflict):
            await review.confirm(actor(), token=latest_token)
        with pytest.raises(InventoryConflict):
            await review.confirm(actor(), token=latest_token)
        assert fake.transfer.await_count == 1

    asyncio.run(scenario())


def test_bad_amount_unknown_warehouse_and_unit_never_issue_ticket() -> None:
    async def scenario() -> None:
        fake = inventory()
        review = MiniAppStockTransferReview(fake, enabled=True)
        for change in (
            {"quantity": "-5"},
            {"quantity": "0"},
            {"quantity": "1.0001"},
            {"quantity": "NaN"},
            {"source": "Kənar", "destination": "Kənar"},
            {"unit": "BOX"},
        ):
            with pytest.raises(InventoryValidationError):
                await review.preview(actor(), **(params() | change))
        with pytest.raises(InventoryConflict):
            await review.preview(actor(), **(params() | {"source": "Naməlum"}))
        with pytest.raises(InventoryConflict):
            await review.preview(actor(), **(params() | {"quantity": "99"}))
        assert fake.transfer.await_count == 0
        assert review._pending == {}

    asyncio.run(scenario())
