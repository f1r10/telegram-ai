"""Phase 12 unit tests: reminder confirmation UX, commands and delivery ledger."""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Bot, InlineKeyboardMarkup, Message, Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from office_assistant.ai.service import InProcessRateGuard
from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.reminders import (
    PendingProposalStore,
    ReminderProposal,
    ReminderService,
)
from office_assistant.application.user_management import UserManagementService
from office_assistant.core.clock import Clock
from office_assistant.infrastructure.database.models import (
    OutboxEvent,
    OutboxEventStatus,
    Reminder,
    ReminderDelivery,
    ReminderDeliveryStatus,
    ReminderStatus,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.handlers import (
    SERVICES_KEY,
    TelegramServices,
    _try_reminder_proposal,  # pyright: ignore[reportPrivateUsage]
    handle_delete_callback,
    handle_delete_data,
    handle_help,
    handle_password_text,
    handle_reminder_callback,
    handle_reminders,
)
from office_assistant.integrations.telegram.outbox import (
    _send_one,  # pyright: ignore[reportPrivateUsage]
)
from office_assistant.integrations.telegram.reminder_format import when_azerbaijani
from office_assistant.integrations.telegram.state import (
    DeleteConfirmationState,
    PasswordAttemptLimiter,
    PasswordEntryState,
    ReminderEditState,
)
from office_assistant.internal_evidence.service import InternalEvidenceService

# Wednesday 2026-10-07, 06:30 Asia/Baku.
NOW_UTC = datetime(2026, 10, 7, 2, 30, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime = NOW_UTC) -> None:
        self._now = now

    def now_utc(self) -> datetime:
        return self._now


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _user(*, user_id: int = 1, telegram_id: int = 123) -> User:
    return User(
        id=user_id,
        telegram_user_id=telegram_id,
        status=UserStatus.ACTIVE,
        role=UserRole.USER,
        timezone="Asia/Baku",
    )


class StubReminderService(ReminderService):
    """Deterministic parsing with the database-backed writes replaced by memory."""

    def __init__(self, clock: Clock | None = None) -> None:
        super().__init__(
            cast(async_sessionmaker[AsyncSession], object()), clock=clock or FixedClock()
        )
        self.proposals: list[ReminderProposal] = []
        self.saved: list[ReminderProposal] = []
        self.reminders: list[Reminder] = []
        self.cancelled_ids: list[int] = []
        self.delete_calls = 0

    def propose(self, user: User, message_text: str) -> ReminderProposal | None:
        proposal = super().propose(user, message_text)
        if proposal is not None:
            self.proposals.append(proposal)
        return proposal

    def repropose(
        self, user: User, previous_token: str, new_message_text: str
    ) -> ReminderProposal | None:
        proposal = super().repropose(user, previous_token, new_message_text)
        if proposal is not None:
            self.proposals.append(proposal)
        return proposal

    async def confirm(self, user: User, token: str) -> int | None:  # type: ignore[override]
        proposal = self.pending.pop(token, user.id)
        if proposal is None:
            return None
        self.saved.append(proposal)
        return len(self.saved)

    async def list_active(self, user: User) -> list[Reminder]:  # type: ignore[override]
        return [row for row in self.reminders if row.user_id == user.id]

    async def cancel(self, user: User, reminder_id: int) -> bool:  # type: ignore[override]
        del user
        self.cancelled_ids.append(reminder_id)
        return reminder_id == 7

    async def delete_personal_data(self, user: User) -> dict[str, int]:  # type: ignore[override]
        self.delete_calls += 1
        return {"conversations": 3, "reminders": 2, "sessions": 1}


@dataclass
class FakeMessage:
    text: str | None = None
    chat_id: int = 5
    message_id: int = 7
    replies: list[str] = field(default_factory=lambda: list[str]())
    markups: list[object] = field(default_factory=lambda: list[object]())

    async def reply_text(self, text: str, **kwargs: object) -> None:
        self.replies.append(text)
        self.markups.append(kwargs.get("reply_markup"))


@dataclass
class FakeChat:
    type: str = ChatType.PRIVATE
    id: int = 5


@dataclass
class FakeQuery:
    data: str | None
    answered: bool = False
    edited: list[str] = field(default_factory=lambda: list[str]())

    async def answer(self) -> None:
        self.answered = True

    async def edit_message_text(self, text: str, **kwargs: object) -> None:
        self.edited.append(text)


@dataclass
class FakeUpdate:
    effective_user: TelegramUser
    effective_chat: FakeChat
    effective_message: FakeMessage | None = None
    callback_query: FakeQuery | None = None
    update_id: int = 1
    effective_id: int = 5


@dataclass
class FakeContext:
    bot_data: dict[str, object]


def _services(user: User | None, reminders: ReminderService | None = None) -> TelegramServices:
    return TelegramServices(
        authentication=cast(AuthenticationService, SimpleNamespace()),
        users=cast(
            UserManagementService,
            SimpleNamespace(
                get_by_telegram_id=AsyncMock(return_value=user),
                touch_active_user=AsyncMock(),
            ),
        ),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        reminders=reminders,
        reminder_edit_state=ReminderEditState(),
        delete_confirmation_state=DeleteConfirmationState(),
    )


def _context(services: TelegramServices) -> FakeContext:
    return FakeContext(bot_data={SERVICES_KEY: services})


def _run(
    handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]],
    update: FakeUpdate,
    services: TelegramServices,
) -> None:
    asyncio.run(handler(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, _context(services))))


def _text_update(text: str, telegram_id: int = 123) -> FakeUpdate:
    return FakeUpdate(
        effective_user=TelegramUser(telegram_id, "Test", False, username="test"),
        effective_chat=FakeChat(),
        effective_message=FakeMessage(text=text),
    )


def _callback_update(data: str, telegram_id: int = 123) -> FakeUpdate:
    return FakeUpdate(
        effective_user=TelegramUser(telegram_id, "Test", False, username="test"),
        effective_chat=FakeChat(),
        callback_query=FakeQuery(data=data),
    )


def _buttons(markup: object) -> list[str]:
    assert isinstance(markup, InlineKeyboardMarkup)
    data: list[str] = []
    for row in markup.inline_keyboard:
        for button in row:
            data.append(str(getattr(button, "callback_data", "") or ""))
    return data


def _allow(_user_id: int) -> bool:
    return True


def _query_edits(update: FakeUpdate) -> list[str]:
    query = cast(FakeQuery, update.callback_query)
    assert query.answered
    return query.edited


def test_when_azerbaijani_uses_azerbaijani_weekday_and_user_timezone() -> None:
    assert (
        when_azerbaijani(utc("2026-10-08T05:00:00"), "Asia/Baku")
        == "cümə axşamı, 08.10.2026 09:00 (Asia/Baku)"
    )
    assert when_azerbaijani(utc("2026-10-08T05:00:00"), "Europe/London").startswith(
        "cümə axşamı, 08.10.2026 06:00 (Europe/London)"
    )


def test_proposal_store_is_owner_scoped_and_bounded_by_ttl() -> None:
    proposal = ReminderProposal(
        token="abc",
        owner_user_id=1,
        reminder_text="Əliyə zəng etməyi",
        due_at_utc=utc("2026-10-08T05:00:00"),
        timezone_name="Asia/Baku",
        needs_clarification=False,
        clarification_reason=None,
    )
    store = PendingProposalStore(ttl_seconds=1800.0)
    store.put(proposal)
    assert store.get("abc", 1) == proposal
    assert store.get("abc", 2) is None
    assert store.pop("abc", 1) == proposal
    assert store.get("abc", 1) is None

    expired = PendingProposalStore(ttl_seconds=-1.0)
    expired.put(proposal)
    assert expired.get("abc", 1) is None


def test_service_proposes_utc_instant_and_reproposes_only_the_time() -> None:
    service = StubReminderService()
    user = _user()
    proposal = service.propose(user, "sabah saat 9-da Əliyə zəng etməyi xatırlat")
    assert proposal is not None
    assert proposal.due_at_utc == utc("2026-10-08T05:00:00")
    assert proposal.timezone_name == "Asia/Baku"
    assert proposal.needs_clarification is False
    assert proposal.reminder_text == "Əliyə zəng etməyi"

    edited = service.repropose(user, proposal.token, "2 saatdan sonra")
    assert edited is not None
    assert edited.token != proposal.token
    assert edited.reminder_text == "Əliyə zəng etməyi"
    assert edited.due_at_utc == utc("2026-10-07T04:30:00")
    assert service.pending.get(proposal.token, user.id) is None


def test_reminder_text_without_time_is_never_stored() -> None:
    service = StubReminderService()
    assert service.propose(_user(), "Əliyə zəng etməyi xatırlat") is None
    assert service.proposals == []


def test_proposal_asks_for_confirmation_in_azerbaijani_without_saving() -> None:
    service = StubReminderService()
    services = _services(_user(), service)
    message = FakeMessage(text="sabah saat 9-da Əliyə zəng etməyi xatırlat")

    handled = asyncio.run(
        _try_reminder_proposal(
            cast(Message, message),
            services,
            _user(),
            "sabah saat 9-da Əliyə zəng etməyi xatırlat",
        )
    )
    assert handled is True
    assert service.saved == []
    assert "cümə axşamı, 08.10.2026 09:00 (Asia/Baku)" in message.replies[0]
    token = service.proposals[0].token
    assert set(_buttons(message.markups[0])) == {
        f"rp:ok:{token}",
        f"rp:e:{token}",
        f"rp:n:{token}",
    }


def test_confirm_edit_and_cancel_flow_through_telegram_updates() -> None:
    service = StubReminderService()
    services = _services(_user(), service)
    answer = AsyncMock()
    services.internal_evidence = cast(InternalEvidenceService, SimpleNamespace(answer=answer))
    services.rate_guard = cast(InProcessRateGuard, SimpleNamespace(allow=_allow))

    first = _text_update("sabah saat 9-da Əliyə zəng etməyi xatırlat")
    _run(handle_password_text, first, services)
    token = service.proposals[0].token
    assert service.saved == []

    edit_request = _callback_update(f"rp:e:{token}")
    _run(handle_reminder_callback, edit_request, services)
    assert _query_edits(edit_request) == [messages.REMINDER_EDIT_PROMPT]
    assert services.reminder_edit_state.token_for(123) == token

    follow_up = _text_update("birisigün axşam 7-də")
    _run(handle_password_text, follow_up, services)
    assert answer.await_count == 0
    assert len(service.proposals) == 2
    assert service.proposals[1].reminder_text == "Əliyə zəng etməyi"
    assert service.proposals[1].due_at_utc == utc("2026-10-09T15:00:00")

    confirm = _callback_update(f"rp:ok:{service.proposals[1].token}")
    _run(handle_reminder_callback, confirm, services)
    saved_text = _query_edits(confirm)[0]
    assert saved_text.startswith("Xatırlatma yadda saxlanıldı")
    assert "cümə, 09.10.2026 19:00 (Asia/Baku)" in saved_text
    assert len(service.saved) == 1

    discard = _callback_update(f"rp:n:{token}")
    _run(handle_reminder_callback, discard, services)
    assert _query_edits(discard) == [messages.REMINDER_INVALID_LINK]


def test_ambiguous_proposal_states_the_open_question() -> None:
    service = StubReminderService()
    services = _services(_user(), service)
    update = _text_update("bazar ertəsi səhər poçtu yoxlamağı xatırlat")
    _run(handle_password_text, update, services)
    message = cast(FakeMessage, update.effective_message)
    assert "Vaxt birmənalı deyil" in message.replies[0]
    assert service.saved == []


def test_unknown_proposal_token_is_rejected() -> None:
    service = StubReminderService()
    services = _services(_user(), service)
    update = _callback_update("rp:ok:deadbeef")
    _run(handle_reminder_callback, update, services)
    assert _query_edits(update) == [messages.REMINDER_INVALID_LINK]
    assert service.saved == []


def test_reminders_list_offers_cancel_and_cancels_only_due_rows() -> None:
    service = StubReminderService()
    service.reminders = [
        Reminder(
            id=7,
            user_id=1,
            text="Hesabatı göndərməyi",
            timezone="Asia/Baku",
            due_at_utc=utc("2026-10-09T05:00:00"),
            status=ReminderStatus.ACTIVE.value,
        ),
        Reminder(
            id=8,
            user_id=1,
            text="Zəng etməyi",
            timezone="Asia/Baku",
            due_at_utc=utc("2026-10-10T05:00:00"),
            status=ReminderStatus.PAUSED.value,
        ),
    ]
    services = _services(_user(), service)
    update = _text_update("/hatirlatmalar")
    _run(handle_reminders, update, services)
    message = cast(FakeMessage, update.effective_message)
    assert message.replies[0] == messages.REMINDER_LIST_HEADER.format(count=2)
    assert "cümə, 09.10.2026 09:00 (Asia/Baku)" in message.replies[1]
    assert messages.REMINDER_LIST_PAUSED in message.replies[2]
    assert _buttons(message.markups[2]) == ["rh:c:8"]

    cancel = _callback_update("rh:c:7")
    _run(handle_reminder_callback, cancel, services)
    assert _query_edits(cancel) == [messages.REMINDER_CANCELLED]
    assert service.cancelled_ids == [7]

    other = _callback_update("rh:c:9")
    _run(handle_reminder_callback, other, services)
    assert _query_edits(other) == [messages.REMINDER_NOT_CANCELLABLE]


def test_reminders_list_reports_no_active_reminders() -> None:
    services = _services(_user(), StubReminderService())
    update = _text_update("/hatirlatmalar")
    _run(handle_reminders, update, services)
    assert cast(FakeMessage, update.effective_message).replies == [messages.REMINDER_NONE]


def test_help_lists_commands_and_ignores_group_chats() -> None:
    services = _services(_user(), StubReminderService())
    update = _text_update("/help")
    _run(handle_help, update, services)
    text = cast(FakeMessage, update.effective_message).replies[0]
    for command in ("/baza", "/hatirlatmalar", "/saxla", "/verilerimi_sil", "/help"):
        assert command in text

    group = FakeUpdate(
        effective_user=TelegramUser(123, "Test", False),
        effective_chat=FakeChat(type=ChatType.GROUP),
        effective_message=FakeMessage(text="/help"),
    )
    _run(handle_help, group, services)
    assert cast(FakeMessage, group.effective_message).replies == []


def test_personal_data_deletion_requires_confirmation() -> None:
    service = StubReminderService()
    services = _services(_user(), service)

    request = _text_update("/verilerimi_sil")
    _run(handle_delete_data, request, services)
    message = cast(FakeMessage, request.effective_message)
    assert message.replies == [messages.DATA_DELETE_CONFIRM]
    assert set(_buttons(message.markups[0])) == {"rd:yes", "rd:no"}

    declined = _callback_update("rd:no")
    _run(handle_delete_callback, declined, services)
    assert _query_edits(declined) == [messages.DATA_DELETE_CANCELLED]
    assert service.delete_calls == 0

    reconfirm = _text_update("/verilerimi_sil")
    _run(handle_delete_data, reconfirm, services)
    confirmed = _callback_update("rd:yes")
    _run(handle_delete_callback, confirmed, services)
    assert service.delete_calls == 1
    assert _query_edits(confirmed) == [
        messages.DATA_DELETED.format(conversations=3, reminders=2, sessions=1)
    ]

    stale = _callback_update("rd:yes")
    _run(handle_delete_callback, stale, services)
    assert _query_edits(stale) == [messages.DATA_DELETE_NO_REQUEST]
    assert service.delete_calls == 1


class _Result:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _FakeOutboxSession:
    def __init__(self, event: OutboxEvent, delivery: ReminderDelivery, reminder: Reminder) -> None:
        self.event = event
        self.delivery = delivery
        self.reminder = reminder

    def begin(self) -> "_FakeOutboxSession":
        return self

    async def __aenter__(self) -> "_FakeOutboxSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, statement: object) -> _Result:
        return _Result(self.event)

    async def get(self, model: object, primary_key: object) -> object:
        if model is ReminderDelivery:
            return self.delivery
        if model is Reminder:
            return self.reminder
        return None


def _reminder_event() -> OutboxEvent:
    return OutboxEvent(
        id=1,
        event_type="REMINDER_DUE",
        aggregate_type="reminder",
        aggregate_id="42",
        recipient_telegram_id=123,
        payload={"reminder_id": 42, "delivery_id": 9, "text": "Hesabatı göndərməyi"},
        status=OutboxEventStatus.PENDING.value,
        attempt_count=0,
        available_at=NOW_UTC,
    )


def _delivery() -> ReminderDelivery:
    return ReminderDelivery(
        id=9,
        reminder_id=42,
        scheduled_for=utc("2026-10-08T05:00:00"),
        status=ReminderDeliveryStatus.CLAIMED.value,
        attempt_count=0,
        claimed_at=NOW_UTC,
    )


def _reminder() -> Reminder:
    return Reminder(
        id=42,
        user_id=1,
        text="Hesabatı göndərməyi",
        timezone="Asia/Baku",
        due_at_utc=utc("2026-10-08T05:00:00"),
        status=ReminderStatus.PAUSED.value,
    )


def _factory(session: _FakeOutboxSession) -> async_sessionmaker[AsyncSession]:
    return cast(async_sessionmaker[AsyncSession], SimpleNamespace(begin=lambda: session))


def test_reminder_delivery_ledger_reaches_sent() -> None:
    session = _FakeOutboxSession(_reminder_event(), _delivery(), _reminder())
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=991)))

    assert asyncio.run(_send_one(cast(Bot, bot), _factory(session))) is True
    assert session.event.status == OutboxEventStatus.SENT
    assert session.delivery.status == ReminderDeliveryStatus.SENT
    assert session.delivery.telegram_message_id == 991
    assert session.reminder.status == ReminderStatus.SENT
    send = cast(AsyncMock, bot.send_message)
    assert send.await_args is not None
    assert send.await_args.kwargs["text"] == messages.REMINDER_DELIVERED.format(
        text="Hesabatı göndərməyi"
    )


def test_reminder_delivery_ledger_retries_then_fails() -> None:
    session = _FakeOutboxSession(_reminder_event(), _delivery(), _reminder())
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=TelegramError("boom")))

    asyncio.run(_send_one(cast(Bot, bot), _factory(session)))
    assert session.delivery.status == ReminderDeliveryStatus.RETRY_PENDING
    assert session.delivery.attempt_count == 1
    assert session.event.status == OutboxEventStatus.PENDING

    asyncio.run(_send_one(cast(Bot, bot), _factory(session)))
    asyncio.run(_send_one(cast(Bot, bot), _factory(session)))
    assert session.event.attempt_count == 3
    assert session.event.status == OutboxEventStatus.FAILED
    assert session.delivery.status == ReminderDeliveryStatus.FAILED
    assert session.reminder.status == ReminderStatus.PAUSED
