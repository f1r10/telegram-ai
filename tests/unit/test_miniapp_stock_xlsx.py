"""Bounded XLSX preview and actor-bound atomic stock approval."""

import asyncio
from io import BytesIO
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from openpyxl import Workbook

from office_assistant.application.inventory import (
    InventoryConflict,
    InventoryLedgerService,
    InventoryValidationError,
    StockEntry,
)
from office_assistant.infrastructure.database.models import User, UserRole, UserStatus
from office_assistant.miniapp.stock_xlsx import MiniAppStockXlsxReview


def actor(role: UserRole = UserRole.ADMIN, *, id: int = 41) -> User:
    return User(id=id, telegram_user_id=1001, role=role, status=UserStatus.ACTIVE)


def workbook(*, formula: bool = False) -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["product_code", "warehouse", "kind", "quantity", "reference"])
    sheet.append(["KA45", "Əsas", "AÇILIŞ", "=1+1" if formula else 2, "OPEN-01"])
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def ledger() -> SimpleNamespace:
    return SimpleNamespace(
        record_batch=AsyncMock(
            return_value=(
                StockEntry("KA45", "Əsas", "OPENING", 2, 2, "OPEN-01"),
            )
        )
    )


def test_workbook_preview_never_changes_stock_and_is_gated() -> None:
    async def scenario() -> None:
        fake = ledger()
        review = MiniAppStockXlsxReview(cast(InventoryLedgerService, fake))
        with pytest.raises(PermissionError):
            await review.preview(actor(), workbook())
        review = MiniAppStockXlsxReview(cast(InventoryLedgerService, fake), enabled=True)
        with pytest.raises(PermissionError):
            await review.preview(actor(UserRole.USER), workbook())
        summary = await review.preview(actor(), workbook())
        assert summary["rows"] == 1
        assert summary["openings"] == 1
        assert isinstance(summary["items"], list)
        assert fake.record_batch.await_count == 0

    asyncio.run(scenario())


def test_same_actor_ticket_single_use_and_cancel_has_no_writes() -> None:
    async def scenario() -> None:
        fake = ledger()
        review = MiniAppStockXlsxReview(cast(InventoryLedgerService, fake), enabled=True)
        preview = await review.preview(actor(), workbook())
        token = str(preview["token"])
        with pytest.raises(InventoryConflict):
            await review.confirm(actor(id=42), token)
        with pytest.raises(InventoryConflict):
            await review.preview(actor(), workbook())
        rows = await asyncio.gather(
            review.confirm(actor(), token),
            review.confirm(actor(), token),
            return_exceptions=True,
        )
        assert sum(isinstance(x, InventoryConflict) for x in rows) == 1
        assert sum(isinstance(x, dict) for x in rows) == 1
        assert fake.record_batch.await_count == 1
        next_preview = await review.preview(actor(), workbook())
        assert await review.cancel(actor(), str(next_preview["token"])) == {
            "status": "cancelled"
        }
        with pytest.raises(InventoryConflict):
            await review.confirm(actor(), str(next_preview["token"]))

    asyncio.run(scenario())


def test_formula_and_bad_xlsx_are_rejected_before_ticket() -> None:
    async def scenario() -> None:
        fake = ledger()
        review = MiniAppStockXlsxReview(cast(InventoryLedgerService, fake), enabled=True)
        with pytest.raises(InventoryValidationError):
            await review.preview(actor(), workbook(formula=True))
        with pytest.raises(InventoryValidationError):
            await review.preview(actor(), b"corrupted")
        with pytest.raises(InventoryValidationError):
            await review.preview(actor(), b"x" * (5 * 1024 * 1024 + 1))
        assert fake.record_batch.await_count == 0

    asyncio.run(scenario())
