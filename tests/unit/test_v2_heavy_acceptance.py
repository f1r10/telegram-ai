"""High-volume offline regression suite for Telegram, source tickets and auth.

No user messages, provider calls, persistent DB records, or real bot tokens.
"""

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from telegram import Chat, Message, Update, User
from telegram.constants import ChatType

from office_assistant.application.reminders import PendingProposalStore, ReminderProposal
from office_assistant.integrations.telegram.catalog_source_tickets import (
    issue_catalog_ticket,
    resolve_catalog_ticket,
)
from office_assistant.integrations.telegram.update_processor import UserSerialUpdateProcessor
from office_assistant.miniapp.auth import (
    MiniAppAuthenticationError,
    verify_telegram_init_data,
)
from office_assistant.processing.catalog_visual_candidates import PdfPageCandidate


def _update(user_id: int, identifier: int) -> Update:
    user = User(id=user_id, first_name="Synthetic", is_bot=False)
    message = Message(
        message_id=identifier,
        date=datetime(2026, 10, 25, tzinfo=UTC),
        chat=Chat(id=user_id, type=ChatType.PRIVATE),
        from_user=user,
    )
    return Update(update_id=identifier, message=message)


def test_24_user_480_update_burst_is_bounded_and_serial_per_user() -> None:
    async def scenario() -> tuple[int, int]:
        global_active = 0
        global_peak = 0
        per_user: dict[int, int] = {}
        completed = 0

        async def work(user_id: int) -> None:
            nonlocal global_active, global_peak, completed
            global_active += 1
            global_peak = max(global_peak, global_active)
            per_user[user_id] = per_user.get(user_id, 0) + 1
            assert global_active <= 8
            assert per_user[user_id] == 1
            try:
                await asyncio.sleep(0.002)
            finally:
                per_user[user_id] -= 1
                global_active -= 1
                completed += 1

        async with UserSerialUpdateProcessor(8) as processor:
            tasks = [
                asyncio.create_task(
                    processor.process_update(
                        _update(10000 + user_id, user_id * 20 + number),
                        work(10000 + user_id),
                    )
                )
                for number in range(20)
                for user_id in range(24)
            ]
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)
        assert all(value == 0 for value in per_user.values())
        return completed, global_peak

    completed, peak = asyncio.run(scenario())
    assert completed == 480
    assert 2 <= peak <= 8


def test_1500_reminder_drafts_never_cross_owners_or_bypass_capacity() -> None:
    pending = PendingProposalStore(max_entries=1000)
    due = datetime(2026, 12, 1, tzinfo=UTC)
    for number in range(1500):
        pending.put(
            ReminderProposal(
                token=f"synthetic-{number}",
                owner_user_id=number % 30 + 1,
                reminder_text=f"Reminder {number}",
                due_at_utc=due,
                timezone_name="Asia/Baku",
                needs_clarification=False,
                clarification_reason=None,
            )
        )
    assert pending.get("synthetic-0", 1) is None
    for number in range(500, 1500):
        token = f"synthetic-{number}"
        owner = number % 30 + 1
        assert pending.get(token, owner) is not None
        assert pending.get(token, owner + 100) is None
    for number in range(500, 1500):
        token = f"synthetic-{number}"
        owner = number % 30 + 1
        assert pending.pop(token, owner + 100) is None
        assert pending.pop(token, owner) is not None
        assert pending.pop(token, owner) is None


def test_256_catalog_pdf_tickets_remain_actor_and_source_bound() -> None:
    match = PdfPageCandidate(
        "synthetic.pdf",
        page=3,
        image_index=5,
        source_file_id=77,
        occurrence_id=91,
    )
    tickets = [issue_catalog_ticket(2000 + index, (match,)) for index in range(256)]
    assert all(ticket is not None for ticket in tickets)
    seen: set[str] = set()
    for index, ticket in enumerate(tickets):
        assert ticket is not None
        assert ticket.token not in seen
        seen.add(ticket.token)
        actor = 2000 + index
        good = resolve_catalog_ticket(ticket, actor, 1, token=ticket.token)
        assert good is not None
        assert good.file_id == 77 and good.occurrence_id == 91
        assert resolve_catalog_ticket(ticket, actor + 1000, 1, token=ticket.token) is None
        assert resolve_catalog_ticket(ticket, actor, 1, token="forged") is None
        assert resolve_catalog_ticket(ticket, actor, 1, now=ticket.expires_at) is None


def _signed_telegram_webapp_data(token: str, *, user_id: int, now: int) -> str:
    fields = {
        "auth_date": str(now),
        "query_id": "synthetic",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    canonical = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_300_signed_miniapp_logins_reject_forged_and_stale_identity() -> None:
    token = "0:synthetic-test-token-not-live"
    now = 1_790_000_000
    for user_id in range(3000, 3300):
        signed = _signed_telegram_webapp_data(token, user_id=user_id, now=now)
        assert verify_telegram_init_data(signed, token, now=now).telegram_user_id == user_id
        with pytest.raises(MiniAppAuthenticationError):
            verify_telegram_init_data(signed, token, now=now + 3601)
        forged = signed.replace(f"%22id%22%3A{user_id}", f"%22id%22%3A{user_id + 1}")
        assert forged != signed
        with pytest.raises(MiniAppAuthenticationError):
            verify_telegram_init_data(forged, token, now=now)
