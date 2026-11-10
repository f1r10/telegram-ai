"""The Sifarişlərim wizard: six durable questions, one review page, one confirmation.

Every case drives a real handler with the same fakes the other adapter tests use, so
what is pinned here is the contract the owner reads on screen: an answer only ever
reaches PostgreSQL through ``OrderService``, an ambiguous reading is asked again, and
nothing becomes an order until Təsdiqlə is pressed (V2 §3.1, contract §0.4).
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from telegram import Update
from telegram import User as TelegramUser
from telegram.constants import ChatType

from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.errors import InvalidBusinessValue, InvalidStateTransition
from office_assistant.application.orders import OrderIntakeError, OrderService, OrderView
from office_assistant.application.user_management import UserManagementService
from office_assistant.infrastructure.database.models import (
    Order,
    OrderState,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.integrations.telegram import messages, orders_format
from office_assistant.integrations.telegram.handlers import (
    MENU_SELECTION_KEY,  # pyright: ignore[reportPrivateUsage]
    ORDER_INTAKE_KEY,  # pyright: ignore[reportPrivateUsage]
    ORDER_INTAKE_TTL_SECONDS,  # pyright: ignore[reportPrivateUsage]
    TelegramServices,
    handle_menu_callback,
    handle_menu_interruption,
    handle_order_callback,
    handle_password_text,
)
from office_assistant.integrations.telegram.state import (
    PasswordAttemptLimiter,
    PasswordEntryState,
)
from office_assistant.orders import workflow
from office_assistant.reminders.time_parsing import ReminderTimeResult

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
DEADLINE = NOW + timedelta(hours=72)
TOKEN = "a28c1f30d4e5f601"
COMPLETE_DRAFT = {
    "deadline": DEADLINE,
    "lead_time": 2880,
    "warnings": 5,
    "requester": "Aysel",
    "description": "10 metr kabel",
}
REVIEW_ROW = [f"od:ok:{TOKEN}", f"od:fix:{TOKEN}", f"od:no:{TOKEN}"]


class FakeUsers:
    def __init__(self, user: User | None) -> None:
        self.user = user

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        if self.user is None or self.user.telegram_user_id != telegram_user_id:
            return None
        return self.user


def _deadline_result(text: str) -> ReminderTimeResult:
    """The one deadline reading the wizard is tested with; anything else asks again."""

    instant = NOW + timedelta(hours=25) if text.strip().lower() == "sabah saat 10-da" else None
    return ReminderTimeResult(
        due_at_utc=instant,
        suggested_due_at_utc=None,
        needs_clarification=instant is None,
        clarification_reason=None if instant else "vaxt ifadəsi tam aydın deyil",
        reminder_text=text,
    )


@dataclass
class FakeOrders:
    """The slice of ``OrderService`` the wizard touches, with the answers it stores."""

    answers: dict[str, object] = field(default_factory=dict[str, object])
    draft_exists: bool = False
    started: int = 0
    confirmed: list[str] = field(default_factory=list[str])
    abandoned: list[str] = field(default_factory=list[str])
    refusals: dict[str, str] = field(default_factory=dict[str, str])
    clock: datetime = NOW

    def _order(self) -> Order:
        return Order(
            id=7,
            created_by_user_id=1,
            intake_token=TOKEN,
            requested_by=self.answers.get("requester"),
            description=str(self.answers.get("description", "")),
            notes=self.answers.get("notes"),
            timezone="Asia/Baku",
            deadline_utc=self.answers.get("deadline"),  # type: ignore[assignment]
            lead_time_minutes=self.answers.get("lead_time"),  # type: ignore[assignment]
            warning_count=self.answers.get("warnings"),  # type: ignore[assignment]
            state=OrderState.PENDING_CONFIRMATION.value,
        )

    async def current_draft(self, user: User) -> Order | None:
        return self._order() if self.draft_exists else None

    async def start(self, user: User) -> Order:
        self.started += 1
        self.draft_exists = True
        return self._order()

    async def next_field(self, user: User, token: str) -> str:
        assert token == TOKEN
        for candidate in workflow.INTAKE_ORDER:
            if candidate not in self.answers:
                return candidate
        raise InvalidStateTransition("the draft is complete, review it instead")

    def deadline_from_text(self, user: User, text: str) -> ReminderTimeResult:
        return _deadline_result(text)

    async def answer(self, user: User, token: str, field_name: str, value: object) -> Order:
        assert token == TOKEN
        if field_name in self.refusals:
            raise InvalidBusinessValue(self.refusals[field_name])
        self.answers[field_name] = value
        return self._order()

    async def review(self, user: User, token: str) -> OrderView:
        missing = next((item for item in workflow.INTAKE_ORDER if item not in self.answers), None)
        if missing is not None and missing != "notes":
            raise OrderIntakeError(missing)
        return OrderView(
            order=self._order(),
            warnings=(),
            materials=(),
            history=(),
            is_overdue=False,
            remaining=None,
        )

    async def confirm(self, user: User, token: str) -> Order:
        if self.confirmed:
            raise InvalidStateTransition("this draft is already an order")
        self.confirmed.append(token)
        order = self._order()
        order.state = OrderState.ACTIVE.value
        return order

    async def abandon(self, user: User, token: str) -> Order:
        self.abandoned.append(token)
        return self._order()

    async def detail(self, user: User, order_id: int) -> OrderView:
        return await self.review(user, TOKEN)

    def now_utc(self) -> datetime:
        return self.clock


def _user(role: UserRole = UserRole.ADMIN) -> User:
    return User(
        id=1,
        telegram_user_id=123,
        role=role,
        status=UserStatus.ACTIVE,
        timezone="Asia/Baku",
    )


def _context(user: User | None, orders: FakeOrders | None) -> SimpleNamespace:
    services = TelegramServices(
        authentication=cast(AuthenticationService, object()),
        users=cast(UserManagementService, FakeUsers(user)),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        orders=cast(OrderService, orders) if orders is not None else None,
    )
    return SimpleNamespace(
        bot_data={"phase2_services": services},
        user_data={},
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
    )


def _message(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        chat_id=123,
        message_id=17,
        reply_text=AsyncMock(),
        photo=None,
        document=None,
        reply_to_message=None,
    )


def _query(data: str) -> SimpleNamespace:
    return SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())


def _update(message: SimpleNamespace, query: SimpleNamespace | None = None) -> Update:
    fake = SimpleNamespace(
        effective_user=TelegramUser(123, "Test", False),
        effective_chat=SimpleNamespace(type=ChatType.PRIVATE, id=123),
        effective_message=message,
        callback_query=query,
        update_id=17,
    )
    return cast(Update, fake)


def _run(handler: object, update: Update, context: SimpleNamespace) -> None:
    asyncio.run(handler(update, cast(object, context)))  # type: ignore[operator]


def _arm(context: SimpleNamespace, field_name: str | None = None) -> None:
    """Stand where the wizard stands after a menu tap: armed and not yet expired."""

    context.user_data[ORDER_INTAKE_KEY] = (TOKEN, float("inf"), field_name)


def _armed(context: SimpleNamespace) -> tuple[str, float, str | None] | None:
    pending = context.user_data.get(ORDER_INTAKE_KEY)
    if pending is None:
        return None
    token, expires_at, field_name = pending
    return str(token), float(expires_at), None if field_name is None else str(field_name)


def _assert_armed(context: SimpleNamespace, *, field_name: str | None) -> None:
    """The wizard must survive one more turn, on the question it just asked."""

    pending = _armed(context)
    assert pending is not None
    token, expires_at, forced = pending
    assert token == TOKEN
    assert forced == field_name
    assert 0.0 < expires_at - monotonic() <= ORDER_INTAKE_TTL_SECONDS


def _reply(message: SimpleNamespace) -> str:
    assert message.reply_text.await_args is not None
    return str(message.reply_text.await_args.args[0])


def _buttons(target: SimpleNamespace, *, edited: bool) -> list[list[str]]:
    call = target.edit_message_text if edited else target.reply_text
    assert call is not None and call.await_args is not None
    markup = call.await_args.kwargs["reply_markup"]
    return [[str(button.callback_data) for button in row] for row in markup.inline_keyboard]


def _typed(context: SimpleNamespace, text: str) -> SimpleNamespace:
    message = _message(text)
    _run(handle_password_text, _update(message), context)
    return message


def _edited(query: SimpleNamespace) -> str:
    assert query.edit_message_text.await_args is not None
    return str(query.edit_message_text.await_args.args[0])


def _plan_view(
    *,
    deadline: datetime | None = DEADLINE,
    lead: int | None = 2880,
    count: int | None = 5,
) -> OrderView:
    """A confirmed-shaped view, so ``plan_text`` can be shown a stale window on purpose."""

    order = Order(
        id=7,
        created_by_user_id=1,
        intake_token=TOKEN,
        requested_by="Aysel",
        description="10 metr kabel",
        notes=None,
        timezone="Asia/Baku",
        deadline_utc=deadline,
        lead_time_minutes=lead,
        warning_count=count,
        state=OrderState.ACTIVE.value,
    )
    return OrderView(
        order=order, warnings=(), materials=(), history=(), is_overdue=False, remaining=None
    )


def test_menu_tap_asks_the_first_question_and_arms_the_wizard() -> None:
    orders = FakeOrders()
    context = _context(_user(), orders)
    query = _query("om:orders")
    _run(handle_menu_callback, _update(_message("/menu"), query), context)

    assert _edited(query) == orders_format.question("deadline")
    _assert_armed(context, field_name=None)


def test_first_tap_on_a_saved_draft_says_it_is_resuming() -> None:
    orders = FakeOrders(answers={"deadline": DEADLINE}, draft_exists=True)
    context = _context(_user(), orders)
    query = _query("om:orders")
    _run(handle_menu_callback, _update(_message("/menu"), query), context)

    assert _edited(query) == (
        f"{messages.ORDERS_INTAKE_RESUMED}\n\n{orders_format.question('lead_time')}"
    )
    assert orders.started == 1


def test_answers_are_stored_in_order_and_the_last_one_opens_the_review() -> None:
    orders = FakeOrders()
    context = _context(_user(), orders)
    _arm(context)

    for answer in ("sabah saat 10-da", "48 saat", "5", "Aysel", "10 metr kabel"):
        _typed(context, answer)
    assert list(orders.answers) == [
        "deadline",
        "lead_time",
        "warnings",
        "requester",
        "description",
    ]

    review = _typed(context, "Zöhl ilə çatdırılsın")
    assert messages.ORDERS_REVIEW_TITLE in _reply(review)
    assert _buttons(review, edited=False) == [REVIEW_ROW]


def test_each_question_shows_its_place_in_the_six() -> None:
    orders = FakeOrders(answers={"deadline": DEADLINE, "lead_time": 2880, "warnings": 5})
    context = _context(_user(), orders)
    _arm(context)

    assert orders_format.question("requester").startswith("Sifariş sualı 4/6")
    assert _reply(_typed(context, "Aysel")) == orders_format.question("description")
    assert orders.answers["requester"] == "Aysel"


def test_unusable_deadline_answer_is_refused_in_azerbaijani_and_asked_again() -> None:
    orders = FakeOrders()
    context = _context(_user(), orders)
    _arm(context, "deadline")

    refused = _reply(_typed(context, "belki"))
    assert refused.startswith(
        messages.ORDERS_ANSWER_REJECTED.format(field=messages.ORDERS_FIELD_DEADLINE)
    )
    assert "vaxt ifadəsi tam aydın deyil" in refused
    assert messages.ORDERS_DEADLINE_PROMPT in refused
    assert "deadline" not in orders.answers
    _assert_armed(context, field_name="deadline")


def test_expired_wizard_never_touches_the_draft() -> None:
    orders = FakeOrders()
    context = _context(_user(), orders)
    context.user_data[ORDER_INTAKE_KEY] = (TOKEN, 0.0, None)

    message = _typed(context, "48 saat")
    assert _reply(message) == messages.ORDERS_INTAKE_EXPIRED
    assert orders.answers == {}
    assert ORDER_INTAKE_KEY not in context.user_data


def test_a_later_menu_tap_disarms_the_wizard_but_keeps_the_draft() -> None:
    orders = FakeOrders(answers={"deadline": DEADLINE})
    context = _context(_user(), orders)
    _arm(context)
    _run(handle_menu_interruption, _update(_message("/menu")), context)

    assert ORDER_INTAKE_KEY not in context.user_data
    assert orders.answers == {"deadline": DEADLINE}


def test_notes_may_be_skipped_and_still_reach_the_review() -> None:
    orders = FakeOrders(answers=dict(COMPLETE_DRAFT))
    context = _context(_user(), orders)
    _arm(context, "notes")

    review = _typed(context, "keç")
    assert "notes" not in orders.answers
    assert messages.ORDERS_REVIEW_TITLE in _reply(review)
    assert _buttons(review, edited=False) == [REVIEW_ROW]


def test_review_page_shows_the_plan_the_confirmation_is_about_to_write() -> None:
    orders = FakeOrders(answers=dict(COMPLETE_DRAFT))
    context = _context(_user(), orders)
    _arm(context, "notes")

    review = _reply(_typed(context, "keç"))
    assert messages.ORDERS_REVIEW_TITLE in review
    assert "Xəbərdarlıq planı" in review
    assert messages.ORDERS_NONE_VALUE in review


def test_confirmation_writes_once_and_shows_the_issued_order() -> None:
    orders = FakeOrders(answers=dict(COMPLETE_DRAFT))
    context = _context(_user(), orders)
    _arm(context)
    query = _query(f"od:ok:{TOKEN}")
    _run(handle_order_callback, _update(_message(""), query), context)

    assert orders.confirmed == [TOKEN]
    assert messages.ORDERS_CONFIRMED.format(id=7) in _edited(query)
    assert ORDER_INTAKE_KEY not in context.user_data


def test_a_second_tap_is_refused_by_the_state_machine_not_by_the_adapter() -> None:
    orders = FakeOrders(answers=dict(COMPLETE_DRAFT))
    context = _context(_user(), orders)
    for _ in range(2):
        _run(handle_order_callback, _update(_message(""), _query(f"od:ok:{TOKEN}")), context)

    assert orders.confirmed == [TOKEN]
    _assert_armed(context, field_name="deadline")


def test_correction_path_re_arms_the_chosen_question() -> None:
    orders = FakeOrders(answers={"deadline": DEADLINE})
    context = _context(_user(), orders)
    query = _query(f"od:fix:{TOKEN}")
    _run(handle_order_callback, _update(_message(""), query), context)

    assert messages.ORDERS_EDIT_PICKER_TITLE in _edited(query)
    assert _buttons(query, edited=True) == [
        [f"od:f:{TOKEN}:deadline", f"od:f:{TOKEN}:lead_time"],
        [f"od:f:{TOKEN}:warnings", f"od:f:{TOKEN}:requester"],
        [f"od:f:{TOKEN}:description", f"od:f:{TOKEN}:notes"],
    ]

    picker = _query(f"od:f:{TOKEN}:lead_time")
    _run(handle_order_callback, _update(_message(""), picker), context)
    assert _edited(picker) == orders_format.question("lead_time")
    _assert_armed(context, field_name="lead_time")


def test_cancel_drops_the_draft_without_confirming_it() -> None:
    orders = FakeOrders(answers={"deadline": DEADLINE})
    context = _context(_user(), orders)
    _arm(context)
    query = _query(f"od:no:{TOKEN}")
    _run(handle_order_callback, _update(_message(""), query), context)

    assert orders.abandoned == [TOKEN]
    assert orders.confirmed == []
    assert _edited(query) == messages.ORDERS_DRAFT_CANCELLED
    assert ORDER_INTAKE_KEY not in context.user_data


@pytest.mark.parametrize("payload", ["", "od:", "sl:ok:x", "od:maybe:x", "od:f:x:unknown"])
def test_a_stale_or_foreign_callback_payload_is_ignored(payload: str) -> None:
    orders = FakeOrders(answers={"deadline": DEADLINE})
    context = _context(_user(), orders)
    _arm(context)
    _run(handle_order_callback, _update(_message(""), _query(payload)), context)

    assert orders.abandoned == []
    assert orders.confirmed == []
    assert orders.answers == {"deadline": DEADLINE}


def test_an_unknown_field_in_a_callback_is_refused_not_assumed() -> None:
    orders = FakeOrders(answers={"deadline": DEADLINE})
    context = _context(_user(), orders)
    query = _query(f"od:f:{TOKEN}:warnings_but_no")
    _run(handle_order_callback, _update(_message(""), query), context)

    assert _edited(query) == messages.ORDERS_INTAKE_INVALID
    assert ORDER_INTAKE_KEY not in context.user_data


def test_the_wizard_is_only_reachable_through_the_authoritative_feature_check() -> None:
    orders = FakeOrders()
    context = _context(_user(role=UserRole.USER), orders)
    query = _query("om:orders")
    _run(handle_menu_callback, _update(_message("/menu"), query), context)

    assert _edited(query) == messages.FEATURE_DISABLED
    assert orders.started == 0
    assert ORDER_INTAKE_KEY not in context.user_data


def test_an_answer_without_the_grant_never_reaches_the_draft() -> None:
    orders = FakeOrders()
    context = _context(_user(role=UserRole.USER), orders)
    _arm(context)

    _typed(context, "48 saat")
    assert orders.answers == {}
    assert ORDER_INTAKE_KEY in context.user_data


@pytest.mark.parametrize(
    "payload",
    [f"od:ok:{TOKEN}", f"od:no:{TOKEN}", f"od:fix:{TOKEN}", f"od:f:{TOKEN}:notes"],
)
def test_a_disabled_user_cannot_drive_the_review_by_replaying_a_callback(payload: str) -> None:
    """The toggle gates the buttons, not only the menu row (docs/ORDERS-WORKFLOW.md)."""

    orders = FakeOrders(answers=dict(COMPLETE_DRAFT))
    context = _context(_user(role=UserRole.USER), orders)
    _arm(context)
    query = _query(payload)

    _run(handle_order_callback, _update(_message(""), query), context)

    assert _edited(query) == messages.FEATURE_DISABLED
    assert orders.confirmed == []
    assert orders.abandoned == []
    assert orders.answers == dict(COMPLETE_DRAFT)
    # The denial is an early return: it must not consume or re-aim the wizard either.
    assert _armed(context) == (TOKEN, float("inf"), None)


def test_a_service_refusal_asks_the_same_question_again() -> None:
    orders = FakeOrders(refusals={"warnings": "warning count must be between 1 and 10"})
    context = _context(_user(), orders)
    _arm(context, "warnings")

    refused = _reply(_typed(context, "15"))
    assert refused.startswith(
        messages.ORDERS_ANSWER_REJECTED.format(field=messages.ORDERS_FIELD_WARNINGS)
    )
    assert messages.ORDERS_WARNINGS_PROMPT in refused
    _assert_armed(context, field_name="warnings")
    assert "warnings" not in orders.answers


def test_a_confirm_tap_on_an_incomplete_draft_asks_for_the_missing_question() -> None:
    orders = FakeOrders(answers={"deadline": DEADLINE})
    context = _context(_user(), orders)
    query = _query(f"od:ok:{TOKEN}")
    _run(handle_order_callback, _update(_message(""), query), context)

    assert _edited(query) == orders_format.question("lead_time")
    _assert_armed(context, field_name="lead_time")


@pytest.mark.parametrize(
    ("answer", "minutes"),
    [("48 saat", 2880), ("2 gün", 2880), ("1 gün 12 saat", 2160), ("90 dəqiqə", 90), ("48", 2880)],
)
def test_the_lead_window_accepts_the_forms_the_owner_types(answer: str, minutes: int) -> None:
    orders = FakeOrders()
    context = _context(_user(), orders)
    _arm(context, "lead_time")

    _typed(context, answer)
    assert orders.answers["lead_time"] == minutes


def test_the_wizard_waits_ten_minutes_for_the_next_answer() -> None:
    assert ORDER_INTAKE_TTL_SECONDS == 600


def test_menu_selection_scope_does_not_leak_into_the_wizard() -> None:
    orders = FakeOrders()
    context = _context(_user(), orders)
    context.user_data[MENU_SELECTION_KEY] = ("internet", float("inf"))
    _run(handle_menu_callback, _update(_message("/menu"), _query("om:orders")), context)

    assert MENU_SELECTION_KEY not in context.user_data


def test_the_plan_waits_for_every_number_before_it_promises_anything() -> None:
    for text in (
        orders_format.plan_text(_plan_view(lead=None), now_utc=NOW),
        orders_format.plan_text(_plan_view(count=None), now_utc=NOW),
        orders_format.plan_text(_plan_view(count=0), now_utc=NOW),
        orders_format.plan_text(_plan_view(deadline=None), now_utc=NOW),
    ):
        assert text == messages.ORDERS_PLAN_PENDING


def test_an_unschedulable_schedule_names_the_reason_instead_of_a_plan() -> None:
    frame = messages.ORDERS_PLAN_INVALID.split("{reason}")[0]

    too_many = orders_format.plan_text(_plan_view(count=11), now_utc=NOW)
    assert too_many.startswith(frame)
    assert "warning count" in too_many

    # A deadline that carries no timezone names no instant, so nothing can be scheduled.
    naive = orders_format.plan_text(_plan_view(deadline=datetime(2026, 11, 12, 9, 0)), now_utc=NOW)
    assert naive.startswith(frame)
    assert "timezone" in naive


def test_a_wholly_future_window_promises_every_instant_and_says_nothing_about_the_past() -> None:
    text = orders_format.plan_text(_plan_view(), now_utc=NOW)

    assert "Xəbərdarlıq planı: 5 xəbərdarlıq" in text
    assert "Göndərilməyə planlaşan say: 5" in text
    assert "keçmişdə qaldığı" not in text


def test_only_the_instants_still_ahead_are_promised_when_the_window_partly_passed() -> None:
    """Four instants over 24 h ending 18 h from now: two are already behind us."""

    text = orders_format.plan_text(
        _plan_view(deadline=NOW + timedelta(hours=18), lead=1440, count=4), now_utc=NOW
    )

    assert messages.ORDERS_PLAN_STALE.format(count=2) in text
    assert "Göndərilməyə planlaşan say: 2" in text
    assert "Birinci:" in text
    assert "Sonuncu:" in text


def test_a_wholly_past_window_counts_every_instant_out_loud_and_promises_none() -> None:
    text = orders_format.plan_text(
        _plan_view(deadline=NOW + timedelta(hours=1), lead=1440, count=4), now_utc=NOW
    )

    assert messages.ORDERS_PLAN_STALE.format(count=4) in text
    assert "Göndərilməyə planlaşan say" not in text
