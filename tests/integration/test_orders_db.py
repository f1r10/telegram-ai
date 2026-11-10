"""Real PostgreSQL evidence for the Orders domain (owner contract V2 §3 and §15).

Mocks cannot prove the parts that matter here: a draft that survives a restart, one
ledger row per scheduled instant, freezing that stops what has not been sent, resuming
that reopens only what is ahead of now, the delivery claim that exactly one worker wins,
and the per-user boundary. Those are PostgreSQL row locks, unique constraints and
cascades, so the tests run against the disposable CI database with a fake clock.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Bot
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError, TimedOut

from office_assistant.application.business_common import FeatureAccessDenied
from office_assistant.application.errors import (
    AuthorizationDenied,
    EntityNotFound,
    InvalidBusinessValue,
)
from office_assistant.application.orders import (
    MAX_LEAD_MINUTES,
    OrderFilter,
    OrderIntakeError,
    OrderService,
)
from office_assistant.auth.features import baseline_grants
from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    FileObject,
    Order,
    OrderAttachment,
    OrderReminder,
    OrderReminderStatus,
    OrderState,
    OutboxEvent,
    OutboxEventStatus,
    User,
    UserFeaturePermission,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.integrations.telegram import messages, order_delivery
from office_assistant.integrations.telegram.outbox import (
    MAX_SEND_ATTEMPTS,
    _send_one,  # pyright: ignore[reportPrivateUsage]
)
from office_assistant.orders import schedule, workflow
from office_assistant.orders.dispatch import (
    ORDER_REMINDER_EVENT_TYPE,
    OrderReminderDispatchService,
)
from office_assistant.repositories.feature_permissions import FeaturePermissionRepository

pytestmark = pytest.mark.integration

CREATOR_TG = 99998231
OTHER_TG = 99998232
LOCKED_TG = 99998233
ADMIN_TG = 99998234
TEST_TG = (CREATOR_TG, OTHER_TG, LOCKED_TG, ADMIN_TG)
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
DEADLINE = NOW + timedelta(hours=72)
ORDER_FILE_SHA = "0" * 63 + "7"
ORDER_FILE_KEY = "orders-test/sifaris.pdf"
# Nothing in a CI database ever reaches this id, so a missing upload can be proven
# without deleting a row another test might still be using.
MISSING_FILE_ID = 10**12

Factory = async_sessionmaker[AsyncSession]


@dataclass
class FakeClock:
    """A clock the test moves, so a schedule is proven without waiting for it."""

    now: datetime = NOW

    def now_utc(self) -> datetime:
        return self.now


Scenario = Callable[[Factory, FakeClock], Awaitable[None]]


def _user(telegram_id: int, *, role: UserRole = UserRole.USER) -> User:
    return User(telegram_user_id=telegram_id, role=role, status=UserStatus.ACTIVE)


def _by_telegram(telegram_id: int) -> Select[tuple[User]]:
    return select(User).where(User.telegram_user_id == telegram_id)


def _scenario(function: Scenario) -> None:
    settings = load_settings()
    if settings.postgres_db != "office_assistant_test":
        pytest.skip("Orders are only written on disposable CI PostgreSQL")
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)

    async def cleanup() -> None:
        async with factory.begin() as session:
            ids = select(User.id).where(User.telegram_user_id.in_(TEST_TG))
            orders = select(Order.id).where(Order.created_by_user_id.in_(ids))
            await session.execute(delete(OrderReminder).where(OrderReminder.order_id.in_(orders)))
            await session.execute(
                delete(OrderAttachment).where(OrderAttachment.order_id.in_(orders))
            )
            await session.execute(delete(AuditEvent).where(AuditEvent.entity_type == "order"))
            await session.execute(
                delete(FileObject).where(
                    or_(
                        FileObject.sha256 == ORDER_FILE_SHA,
                        FileObject.storage_key == ORDER_FILE_KEY,
                    )
                )
            )
            # The consumer reads every event type. As in the other delivery suites,
            # isolate its shared queue on the guarded, disposable CI database only.
            await session.execute(delete(OutboxEvent))
            await session.execute(delete(Order).where(Order.created_by_user_id.in_(ids)))
            await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(ids)))
            await session.execute(
                delete(UserFeaturePermission).where(UserFeaturePermission.user_id.in_(ids))
            )
            await session.execute(delete(User).where(User.telegram_user_id.in_(TEST_TG)))

    async def seeded() -> None:
        """The grid an approval writes, with the new orders capability switched on."""

        async with factory.begin() as session:
            creator = _user(CREATOR_TG)
            other = _user(OTHER_TG)
            locked = _user(LOCKED_TG)
            admin = _user(ADMIN_TG, role=UserRole.ADMIN)
            session.add_all([creator, other, locked, admin])
            await session.flush()
            repository = FeaturePermissionRepository(session)
            grants = {**baseline_grants(), "orders": True}
            for user in (creator, other, admin):
                await repository.insert_baseline(int(user.id), grants)
            await repository.insert_baseline(int(locked.id), grants)
            await repository.upsert(
                user_id=int(locked.id),
                feature_key="orders",
                enabled=False,
                updated_by_user_id=None,
            )

    async def run() -> None:
        clock = FakeClock()
        await cleanup()
        await seeded()
        try:
            await function(factory, clock)
        finally:
            await cleanup()

    asyncio.run(run())
    asyncio.run(engine.dispose())


async def _account(factory: Factory, telegram_id: int) -> User:
    async with factory() as session:
        return cast(User, await session.scalar(_by_telegram(telegram_id)))


async def _creator(factory: Factory) -> User:
    return await _account(factory, CREATOR_TG)


async def _other(factory: Factory) -> User:
    return await _account(factory, OTHER_TG)


async def _admin(factory: Factory) -> User:
    return await _account(factory, ADMIN_TG)


async def _locked(factory: Factory) -> User:
    return await _account(factory, LOCKED_TG)


def _service(factory: Factory, clock: FakeClock) -> OrderService:
    """A fresh service instance is the same thing a restarted bot or worker is."""

    return OrderService(factory, clock=clock)


async def _fill(
    service: OrderService, user: User, token: str, *, notes: str | None = "sabahtan gedin"
) -> None:
    """Answer the six intake questions in the contract's order."""

    await service.answer(user, token, "deadline", DEADLINE)
    await service.answer(user, token, "lead_time", 48 * 60)
    await service.answer(user, token, "warnings", 5)
    await service.answer(user, token, "requester", "Aysel müəllim")
    await service.answer(user, token, "description", "Konferans zalı üçün kabellər")
    await service.answer(user, token, "notes", notes)


async def _confirmed(factory: Factory, clock: FakeClock) -> tuple[User, Order]:
    user = await _creator(factory)
    service = _service(factory, clock)
    draft = await service.start(user)
    token = cast(str, draft.intake_token)
    await _fill(service, user, token)
    return user, await service.confirm(user, token)


async def _ledger(factory: Factory, order_id: int) -> list[OrderReminder]:
    async with factory() as session:
        rows = (
            await session.execute(
                select(OrderReminder)
                .where(OrderReminder.order_id == order_id)
                .order_by(OrderReminder.scheduled_for)
            )
        ).scalars()
        return list(rows.all())


def test_intake_is_durable_and_resumes_into_the_same_draft() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user = await _creator(factory)
        first = _service(factory, clock)
        draft = await first.start(user)
        token = cast(str, draft.intake_token)
        await first.answer(user, token, "deadline", DEADLINE)

        # A restarted bot holds no in-process memory of this conversation at all.
        restarted = _service(factory, clock)
        resumed = await restarted.current_draft(user)
        assert resumed is not None and resumed.id == draft.id
        assert await restarted.next_field(user, token) == "lead_time"

        again = await restarted.start(user)
        assert again.id == draft.id, "one account must not hold two open drafts"

    _scenario(scenario)


def test_confirm_writes_exactly_the_contract_schedule() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        _, order = await _confirmed(factory, clock)

        assert OrderState(order.state) is OrderState.ACTIVE
        assert order.confirmed_at == NOW
        rows = await _ledger(factory, int(order.id))
        assert len(rows) == 5
        expected = schedule.review_instants(
            deadline_utc=DEADLINE, lead_time_minutes=48 * 60, warning_count=5
        )
        assert tuple(row.scheduled_for for row in rows) == expected
        assert rows[1].scheduled_for - rows[0].scheduled_for == timedelta(hours=9, minutes=36)
        assert all(row.status == OrderReminderStatus.PENDING.value for row in rows)
        assert [row.ordinal for row in rows] == [0, 1, 2, 3, 4]
        assert len({row.idempotency_key for row in rows}) == 5

    _scenario(scenario)


def test_confirmation_is_idempotent_and_never_schedules_twice() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user = await _creator(factory)
        service = _service(factory, clock)
        draft = await service.start(user)
        token = cast(str, draft.intake_token)
        await _fill(service, user, token)
        order = await service.confirm(user, token)
        repeated = await service.confirm(user, token)
        # Even another restarted service replaying the same confirmation changes nothing.
        repeated_again = await _service(factory, clock).confirm(user, token)

        assert repeated.id == order.id and repeated_again.id == order.id
        rows = await _ledger(factory, int(order.id))
        assert len(rows) == 5
        assert all(row.status == OrderReminderStatus.PENDING.value for row in rows)

    _scenario(scenario)


def test_warnings_that_are_already_due_are_stored_as_skipped_not_queued() -> None:
    """§3.3: confirming into a short window must not queue a burst of stale texts."""

    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user = await _creator(factory)
        service = _service(factory, clock)
        draft = await service.start(user)
        token = cast(str, draft.intake_token)
        await service.answer(user, token, "deadline", NOW + timedelta(hours=20))
        await service.answer(user, token, "lead_time", 24 * 60)
        await service.answer(user, token, "warnings", 4)
        await service.answer(user, token, "requester", "Elvin")
        await service.answer(user, token, "description", "Baqajev işi")
        order = await service.confirm(user, token)

        rows = await _ledger(factory, int(order.id))
        assert [row.status for row in rows] == [
            OrderReminderStatus.SKIPPED.value,
            OrderReminderStatus.PENDING.value,
            OrderReminderStatus.PENDING.value,
            OrderReminderStatus.PENDING.value,
        ]
        assert rows[0].scheduled_for <= NOW
        assert all(row.scheduled_for > NOW for row in rows[1:])
        view = await service.detail(user, int(order.id))
        assert view.planned == 3
        assert view.skipped == 1

    _scenario(scenario)


def test_freeze_stops_the_ledger_and_resume_reopens_only_the_future() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)

        frozen = await _service(factory, clock).act(
            user, int(order.id), workflow.OrderAction.FREEZE
        )
        assert OrderState(frozen.state) is OrderState.FROZEN
        rows = await _ledger(factory, int(order.id))
        assert all(row.status == OrderReminderStatus.CANCELLED.value for row in rows)

        # Forty hours pass while the order is frozen: two instants fall behind us.
        clock.now = NOW + timedelta(hours=40)
        resumed = await _service(factory, clock).act(
            user, int(order.id), workflow.OrderAction.RESUME
        )
        assert OrderState(resumed.state) is OrderState.ACTIVE
        rows = await _ledger(factory, int(order.id))
        future = [row for row in rows if row.scheduled_for > clock.now]
        past = [row for row in rows if row.scheduled_for <= clock.now]
        assert len(future) == 3
        assert len(past) == 2
        assert all(row.status == OrderReminderStatus.PENDING.value for row in future)
        assert all(row.status == OrderReminderStatus.CANCELLED.value for row in past)

    _scenario(scenario)


def test_complete_and_delete_stop_deliveries_and_leave_the_history_behind() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        service = _service(factory, clock)

        done = await service.act(user, int(order.id), workflow.OrderAction.COMPLETE)
        assert OrderState(done.state) is OrderState.COMPLETED
        assert done.completed_at is not None
        rows = await _ledger(factory, int(order.id))
        assert all(row.status == OrderReminderStatus.CANCELLED.value for row in rows)
        archived = [listed.id for listed in await service.list(user, OrderFilter.ARCHIVED)]
        assert order.id in archived
        assert order.id not in [listed.id for listed in await service.list(user, OrderFilter.OPEN)]

        deleted = await service.act(user, int(order.id), workflow.OrderAction.DELETE)
        assert OrderState(deleted.state) is OrderState.DELETED
        assert deleted.deleted_at is not None
        for view in OrderFilter:
            assert deleted.id not in [listed.id for listed in await service.list(user, view)]
        with pytest.raises(EntityNotFound):
            await service.detail(user, int(deleted.id))
        assert len(await _ledger(factory, int(deleted.id))) == 5, "soft delete keeps the ledger"

    _scenario(scenario)


def test_a_frozen_order_cannot_be_completed_and_a_draft_cannot_be_frozen() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        service = _service(factory, clock)
        await service.act(user, int(order.id), workflow.OrderAction.FREEZE)

        with pytest.raises(workflow.InvalidTransition):
            await service.act(user, int(order.id), workflow.OrderAction.COMPLETE)
        with pytest.raises(workflow.InvalidTransition):
            await service.act(user, int(order.id), workflow.OrderAction.CONFIRM)
        draft = await service.start(user)
        with pytest.raises(workflow.InvalidTransition):
            await service.act(user, int(draft.id), workflow.OrderAction.FREEZE)

    _scenario(scenario)


def test_another_user_cannot_read_or_change_the_order() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        _, order = await _confirmed(factory, clock)
        other = await _other(factory)
        service = _service(factory, clock)
        token = cast(str, order.intake_token)

        with pytest.raises(AuthorizationDenied):
            await service.act(other, int(order.id), workflow.OrderAction.FREEZE)
        with pytest.raises(EntityNotFound):
            await service.detail(other, int(order.id))
        with pytest.raises(AuthorizationDenied):
            await service.answer(other, token, "notes", "kəndə gir")
        assert await service.list(other, OrderFilter.OPEN) == []

    _scenario(scenario)


def test_a_disabled_capability_is_refused_at_the_service_boundary() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        locked = await _locked(factory)
        service = _service(factory, clock)

        with pytest.raises(FeatureAccessDenied):
            await service.start(locked)
        with pytest.raises(FeatureAccessDenied):
            await service.list(locked, OrderFilter.OPEN)

    _scenario(scenario)


def test_an_invalid_answer_never_reaches_the_record() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user = await _creator(factory)
        service = _service(factory, clock)
        draft = await service.start(user)
        token = cast(str, draft.intake_token)

        for field, value in (
            ("deadline", NOW - timedelta(hours=1)),
            ("deadline", "sabah"),
            ("deadline", datetime(2026, 12, 1, 9, 0)),
            ("lead_time", 0),
            ("lead_time", -10),
            ("lead_time", "bir gün"),
            ("lead_time", MAX_LEAD_MINUTES + 1),
            ("warnings", 0),
            ("warnings", 11),
            ("warnings", True),
            ("requester", "   "),
            ("description", ""),
            ("unknown", "x"),
        ):
            with pytest.raises(InvalidBusinessValue):
                await service.answer(user, token, field, value)

        async with factory() as session:
            stored = cast(Order, await session.get(Order, draft.id))
            assert stored.deadline_utc is None
            assert stored.lead_time_minutes is None
            assert stored.warning_count is None
            assert stored.requested_by is None
        with pytest.raises(OrderIntakeError):
            await service.confirm(user, token)

    _scenario(scenario)


def test_the_lead_window_is_capped_at_the_documented_one_year_ceiling() -> None:
    """Q47: the ceiling itself has to be accepted, or a rejected boundary is off by one."""

    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user = await _creator(factory)
        service = _service(factory, clock)
        draft = await service.start(user)
        token = cast(str, draft.intake_token)

        await service.answer(user, token, "lead_time", MAX_LEAD_MINUTES)
        with pytest.raises(InvalidBusinessValue):
            await service.answer(user, token, "lead_time", MAX_LEAD_MINUTES + 1)

        async with factory() as session:
            stored = cast(Order, await session.get(Order, draft.id))
            assert stored.lead_time_minutes == MAX_LEAD_MINUTES

    _scenario(scenario)


def test_an_incomplete_draft_names_the_field_that_is_still_missing() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user = await _creator(factory)
        service = _service(factory, clock)
        draft = await service.start(user)
        token = cast(str, draft.intake_token)
        await service.answer(user, token, "deadline", DEADLINE)

        with pytest.raises(OrderIntakeError) as caught:
            await service.confirm(user, token)
        assert caught.value.field == "lead_time"
        with pytest.raises(OrderIntakeError):
            await service.review(user, token)

    _scenario(scenario)


def test_rescheduling_supersedes_the_old_plan_and_keeps_what_already_happened() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        service = _service(factory, clock)
        rows = await _ledger(factory, int(order.id))
        delivered = rows[0]
        # The delivery engine (Milestone 3) writes this state; the ledger test seeds it.
        async with factory.begin() as session:
            await session.execute(
                update(OrderReminder)
                .where(OrderReminder.id == delivered.id)
                .values(status=OrderReminderStatus.SENT.value, sent_at=NOW)
            )
        clock.now = NOW + timedelta(hours=30)

        scheduled, skipped = await service.reschedule(
            user,
            int(order.id),
            deadline_utc=NOW + timedelta(hours=120),
            lead_time_minutes=24 * 60,
            warning_count=3,
        )
        rows = await _ledger(factory, int(order.id))
        view = await service.detail(user, int(order.id))

        assert (scheduled, skipped) == (3, 0)
        assert len(rows) == 8, "the old plan stays as history"
        by_id = {row.id: row for row in rows}
        assert delivered.id in by_id
        assert by_id[delivered.id].status == OrderReminderStatus.SENT.value, (
            "a warning that already arrived is never rewritten by a new plan"
        )
        assert sum(row.status == OrderReminderStatus.SUPERSEDED.value for row in rows) == 4
        assert sum(row.status == OrderReminderStatus.PENDING.value for row in rows) == 3
        assert view.order.deadline_utc == NOW + timedelta(hours=120)
        assert view.order.warning_count == 3
        assert view.delivered == 1

    _scenario(scenario)


def test_uploaded_materials_keep_their_provenance_in_the_review() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user = await _creator(factory)
        service = _service(factory, clock)
        draft = await service.start(user)
        token = cast(str, draft.intake_token)
        async with factory.begin() as session:
            stored = FileObject(
                storage_key=ORDER_FILE_KEY,
                original_filename="sifaris.pdf",
                content_type="application/pdf",
                byte_size=1024,
                sha256=ORDER_FILE_SHA,
                state="ACTIVE",
            )
            session.add(stored)
            await session.flush()
            file_id = int(stored.id)

        await service.attach(user, token, file_id, telegram_message_id=555)
        await _fill(service, user, token)
        review = await service.review(user, token)

        assert [(item.original_filename, item.position) for item in review.materials] == [
            ("sifaris.pdf", 1)
        ]
        async with factory() as session:
            row = cast(
                OrderAttachment,
                await session.scalar(
                    select(OrderAttachment).where(OrderAttachment.order_id == review.order.id)
                ),
            )
            assert row.added_by_user_id == user.id
            assert row.telegram_message_id == 555
        with pytest.raises(EntityNotFound):
            await service.attach(user, token, MISSING_FILE_ID)

    _scenario(scenario)


def test_creator_and_named_requester_stay_separate_and_overdue_is_derived() -> None:
    """§3.2: the account that typed the order is not the person it was made for."""

    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        service = _service(factory, clock)
        admin = await _admin(factory)

        assert order.created_by_user_id == user.id
        assert order.requested_by == "Aysel müəllim"
        assert order.timezone == "Asia/Baku"
        assert await service.list(admin, OrderFilter.OPEN) == []
        view = await service.detail(user, int(order.id))
        assert view.delivered == 0
        assert view.planned == 5
        assert view.remaining is not None and view.remaining > timedelta(0)
        assert not view.is_overdue
        assert [event.action for event in view.history][0] == "ORDER_CONFIRMED"

        clock.now = DEADLINE + timedelta(minutes=1)
        overdue = await service.detail(user, int(order.id))
        assert overdue.is_overdue
        assert order.id in [listed.id for listed in await service.list(user, OrderFilter.OVERDUE)]

    _scenario(scenario)


def test_removing_an_account_cascades_its_orders_and_ledger() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        _, order = await _confirmed(factory, clock)
        order_id = int(order.id)

        async with factory.begin() as session:
            await session.execute(delete(User).where(User.telegram_user_id == CREATOR_TG))
        async with factory() as session:
            assert await session.scalar(select(Order).where(Order.id == order_id)) is None
            remaining = await session.scalar(
                select(func.count())
                .select_from(OrderReminder)
                .where(OrderReminder.order_id == order_id)
            )
            assert remaining == 0

    _scenario(scenario)


# --------------------------------------------------------------------------------------
# Delivery (master spec §15.3-§15.5): the plan is only worth the message that arrives.
# --------------------------------------------------------------------------------------

# Past the first two instants of the 48h / 5 plan (24h and 33h36 after NOW) and short of
# the third, so one claim pass has exactly two due rows and three that must stay queued.
DUE_AT = NOW + timedelta(hours=34)
# Queue eligibility is read from the wall clock by the consumer, never from the injected
# clock, so the test hands it an instant it cannot refuse instead of waiting out a backoff.
ELIGIBLE = datetime(2020, 1, 1, tzinfo=UTC)


def _delivery(factory: Factory, clock: FakeClock) -> OrderReminderDispatchService:
    """A fresh dispatcher is a restarted worker: nothing may live between passes."""

    return OrderReminderDispatchService(factory, clock=clock)


async def _events(factory: Factory) -> list[OutboxEvent]:
    async with factory() as session:
        rows = (
            await session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.event_type == ORDER_REMINDER_EVENT_TYPE)
                .order_by(OutboxEvent.id)
            )
        ).scalars()
        return list(rows.all())


async def _queue_is_eligible(factory: Factory) -> None:
    async with factory.begin() as session:
        await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.event_type == ORDER_REMINDER_EVENT_TYPE)
            .values(available_at=ELIGIBLE)
        )


def _bot(error: str | None = None) -> SimpleNamespace:
    if error is None:
        return SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=77)))
    return SimpleNamespace(send_message=AsyncMock(side_effect=TelegramError(error)))


def test_a_due_instant_is_claimed_once_and_queued_for_the_creator_only() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        _, order = await _confirmed(factory, clock)
        clock.now = DUE_AT

        assert await _delivery(factory, clock).claim_due() == 2

        rows = await _ledger(factory, int(order.id))
        assert [row.status for row in rows] == [
            OrderReminderStatus.CLAIMED.value,
            OrderReminderStatus.CLAIMED.value,
            OrderReminderStatus.PENDING.value,
            OrderReminderStatus.PENDING.value,
            OrderReminderStatus.PENDING.value,
        ]
        assert all(row.claimed_at == DUE_AT for row in rows[:2])

        events = await _events(factory)
        assert len(events) == 2
        # §15.2: the account that created the order and nobody else — not the named
        # requester, not the rest of the company.
        assert {event.recipient_telegram_id for event in events} == {CREATOR_TG}
        assert {(event.aggregate_type, event.aggregate_id) for event in events} == {
            ("order", str(order.id))
        }
        assert [
            (int(str(event.payload["index"])), int(str(event.payload["total"]))) for event in events
        ] == [(1, 5), (2, 5)]

        # A second pass, or a worker that restarted mid-loop, adds nothing.
        assert await _delivery(factory, clock).claim_due() == 0
        assert len(await _events(factory)) == 2

    _scenario(scenario)


def test_two_workers_claiming_at_once_split_the_due_instants_without_duplicates() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        _, order = await _confirmed(factory, clock)
        clock.now = DUE_AT

        won = await asyncio.gather(
            _delivery(factory, clock).claim_due(),
            _delivery(factory, clock).claim_due(),
        )

        assert sum(won) == 2, "one instant may be claimed by exactly one worker"
        assert len(await _events(factory)) == 2
        rows = await _ledger(factory, int(order.id))
        claimed = [row for row in rows if row.status == OrderReminderStatus.CLAIMED.value]
        assert len(claimed) == 2
        assert len({row.scheduled_for for row in claimed}) == 2

    _scenario(scenario)


@pytest.mark.parametrize(
    "action",
    [workflow.OrderAction.FREEZE, workflow.OrderAction.COMPLETE, workflow.OrderAction.CANCEL],
)
def test_a_stop_after_the_claim_stops_the_message_without_calling_it_a_failure(
    action: workflow.OrderAction,
) -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        clock.now = DUE_AT
        await _delivery(factory, clock).claim_due()
        await _queue_is_eligible(factory)

        await _service(factory, clock).act(user, int(order.id), action)

        bot = _bot()
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
        send = cast(AsyncMock, bot.send_message)
        assert send.await_count == 0, f"{action} must stop a row claimed before it stopped"
        assert [event.status for event in await _events(factory)] == [
            OutboxEventStatus.CANCELLED.value,
            OutboxEventStatus.CANCELLED.value,
        ]
        rows = await _ledger(factory, int(order.id))
        # §15.4: stopping is neither a send nor a failure, so the ledger calls it stopped.
        assert [row.status for row in rows[:2]] == [
            OrderReminderStatus.CANCELLED.value,
            OrderReminderStatus.CANCELLED.value,
        ]

    _scenario(scenario)


def test_a_delivered_reminder_states_the_order_and_settles_the_ledger() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        _, order = await _confirmed(factory, clock)
        clock.now = DUE_AT
        await _delivery(factory, clock).claim_due()
        await _queue_is_eligible(factory)

        bot = _bot()
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is False

        send = cast(AsyncMock, bot.send_message)
        texts = [str(call.kwargs["text"]) for call in send.await_args_list]
        assert len(texts) == 2
        for index, text in enumerate(texts, start=1):
            assert text.startswith(
                messages.ORDERS_REMINDER_SUBJECT.format(order_id=order.id, index=index, total=5)
            )
            assert "Konferans zalı üçün kabellər" in text
            assert "Aysel müəllim" in text
            assert messages.ORDERS_FIELD_DEADLINE in text
            assert messages.ORDERS_FIELD_REMAINING in text
            assert "Asia/Baku" in text
        assert [call.kwargs["chat_id"] for call in send.await_args_list] == [
            CREATOR_TG,
            CREATOR_TG,
        ]

        rows = await _ledger(factory, int(order.id))
        assert [row.status for row in rows] == [
            OrderReminderStatus.SENT.value,
            OrderReminderStatus.SENT.value,
            OrderReminderStatus.PENDING.value,
            OrderReminderStatus.PENDING.value,
            OrderReminderStatus.PENDING.value,
        ]
        assert all(row.sent_at is not None for row in rows[:2])
        assert [event.status for event in await _events(factory)] == [
            OutboxEventStatus.SENT.value,
            OutboxEventStatus.SENT.value,
        ]

    _scenario(scenario)


def test_a_failing_provider_is_retried_within_a_bound_and_never_marked_as_sent() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        _, order = await _confirmed(factory, clock)
        clock.now = DUE_AT
        await _delivery(factory, clock).claim_due()
        await _queue_is_eligible(factory)

        bot = _bot(error="channel is unreachable")
        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
            events = await _events(factory)
            assert events[0].status == (
                OutboxEventStatus.FAILED.value
                if attempt == MAX_SEND_ATTEMPTS
                else OutboxEventStatus.PENDING.value
            )
            rows = await _ledger(factory, int(order.id))
            assert rows[0].attempt_count == attempt
            assert rows[0].last_error == "TelegramError"  # no raw provider text is persisted
            await _queue_is_eligible(factory)

        rows = await _ledger(factory, int(order.id))
        assert rows[0].status == OrderReminderStatus.FAILED.value
        assert rows[1].status == OrderReminderStatus.CLAIMED.value
        assert OrderReminderStatus.SENT.value not in [row.status for row in rows], (
            "a failed delivery is never recorded as sent"
        )

    _scenario(scenario)


def test_an_account_that_cannot_receive_stops_the_entry_instead_of_queueing_it() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        _, order = await _confirmed(factory, clock)
        clock.now = DUE_AT
        async with factory.begin() as session:
            await session.execute(
                update(User)
                .where(User.telegram_user_id == CREATOR_TG)
                .values(status=UserStatus.DISABLED.value)
            )

        assert await _delivery(factory, clock).claim_due() == 0
        assert await _events(factory) == []
        rows = await _ledger(factory, int(order.id))
        assert [row.status for row in rows[:2]] == [
            OrderReminderStatus.CANCELLED.value,
            OrderReminderStatus.CANCELLED.value,
        ]
        # The account stopped receiving, the plan did not: the instants still ahead stay
        # queued, so re-enabling the account resumes the same schedule.
        assert all(row.status == OrderReminderStatus.PENDING.value for row in rows[2:])

    _scenario(scenario)


def test_a_claim_abandoned_by_a_crash_is_closed_from_the_event_instead_of_replayed() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        _, order = await _confirmed(factory, clock)
        clock.now = DUE_AT
        await _delivery(factory, clock).claim_due()

        events = await _events(factory)
        orphan_at = NOW + timedelta(hours=66)
        orphan_key = schedule.idempotency_key(order_id=int(order.id), scheduled_for=orphan_at)
        async with factory.begin() as session:
            delivered = (
                await session.execute(select(OutboxEvent).where(OutboxEvent.id == events[0].id))
            ).scalar_one()
            # The message went out and the process died before its ledger row was settled.
            delivered.status = OutboxEventStatus.SENT.value
            delivered.sent_at = DUE_AT
            rows = (
                (
                    await session.execute(
                        select(OrderReminder).where(OrderReminder.order_id == order.id)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                if row.status == OrderReminderStatus.CLAIMED.value:
                    row.claimed_at = NOW
            # A second row abandoned mid-send whose event never existed at all.
            session.add(
                OrderReminder(
                    order_id=int(order.id),
                    ordinal=1,
                    scheduled_for=orphan_at,
                    status=OrderReminderStatus.SENDING.value,
                    idempotency_key=orphan_key,
                    claimed_at=NOW,
                )
            )

        clock.now = DUE_AT + timedelta(hours=1)
        recovered = await OrderReminderDispatchService(
            factory, clock=clock, lease_seconds=60
        ).reclaim_stale()

        assert recovered == 2, "a row whose event is still queued belongs to the consumer"
        rows = await _ledger(factory, int(order.id))
        # The finished send is settled, not replayed.
        assert rows[0].status == OrderReminderStatus.SENT.value
        assert rows[0].sent_at == DUE_AT
        # The row whose event is still pending is left to the consumer that owns it.
        assert rows[1].status == OrderReminderStatus.CLAIMED.value
        # A claim with no event at all is failed rather than requeued: its instant is in
        # the past here, and §15.4 forbids the burst a replay would produce.
        orphan = next(row for row in rows if row.idempotency_key == orphan_key)
        assert orphan.status == OrderReminderStatus.FAILED.value
        assert orphan.last_error == "claimed without a durable outbox event"

    _scenario(scenario)


def test_a_row_waiting_for_its_retry_is_never_claimed_a_second_time() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        _, order = await _confirmed(factory, clock)
        clock.now = DUE_AT
        await _delivery(factory, clock).claim_due()
        async with factory.begin() as session:
            rows = (
                (
                    await session.execute(
                        select(OrderReminder).where(OrderReminder.order_id == order.id)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                if row.status == OrderReminderStatus.CLAIMED.value:
                    row.status = OrderReminderStatus.RETRY_PENDING.value

        # The retry belongs to the queued event, so a second event for the same instant
        # would be the duplicate message §15.3 rules out.
        assert await _delivery(factory, clock).claim_due() == 0
        assert len(await _events(factory)) == 2

    _scenario(scenario)


@pytest.mark.parametrize(
    "queued_status",
    [OrderReminderStatus.CLAIMED, OrderReminderStatus.SENDING, OrderReminderStatus.RETRY_PENDING],
)
def test_freeze_resume_never_revives_queued_deliveries(
    queued_status: OrderReminderStatus,
) -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        service = _service(factory, clock)
        clock.now = DUE_AT
        assert await _delivery(factory, clock).claim_due() == 2
        async with factory.begin() as session:
            await session.execute(
                update(OrderReminder)
                .where(
                    OrderReminder.order_id == order.id,
                    OrderReminder.status == OrderReminderStatus.CLAIMED.value,
                )
                .values(status=queued_status.value)
            )

        await service.act(user, int(order.id), workflow.OrderAction.FREEZE)
        assert all(
            row.status == OrderReminderStatus.CANCELLED.value
            for row in await _ledger(factory, int(order.id))
        )
        # Lifecycle never locks or modifies the outbox; its consumer owns suppression.
        assert [event.status for event in await _events(factory)] == [
            OutboxEventStatus.PENDING.value,
            OutboxEventStatus.PENDING.value,
        ]
        await service.act(user, int(order.id), workflow.OrderAction.RESUME)
        rows = await _ledger(factory, int(order.id))
        assert [row.status for row in rows[:2]] == [
            OrderReminderStatus.CANCELLED.value,
            OrderReminderStatus.CANCELLED.value,
        ]
        assert all(row.status == OrderReminderStatus.PENDING.value for row in rows[2:])
        assert all(row.claimed_at is None for row in rows[2:])

        await _queue_is_eligible(factory)
        bot = _bot()
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
        assert cast(AsyncMock, bot.send_message).await_count == 0
        assert all(
            event.status == OutboxEventStatus.CANCELLED.value for event in await _events(factory)
        )
        # A genuinely future warning still gets its own delivery after resuming.
        clock.now = rows[2].scheduled_for
        assert await _delivery(factory, clock).claim_due() == 1
        await _queue_is_eligible(factory)
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
        assert cast(AsyncMock, bot.send_message).await_count == 1
        rows = await _ledger(factory, int(order.id))
        assert rows[2].status == OrderReminderStatus.SENT.value
        assert all(row.status == OrderReminderStatus.CANCELLED.value for row in rows[:2])

    _scenario(scenario)


@pytest.mark.parametrize(
    "queued_status",
    [OrderReminderStatus.CLAIMED, OrderReminderStatus.SENDING, OrderReminderStatus.RETRY_PENDING],
)
def test_reschedule_supersedes_due_claims_sends_and_retries(
    queued_status: OrderReminderStatus,
) -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        clock.now = DUE_AT
        assert await _delivery(factory, clock).claim_due() == 2
        await _queue_is_eligible(factory)
        old_rows = await _ledger(factory, int(order.id))
        if queued_status is OrderReminderStatus.RETRY_PENDING:
            assert (
                await _send_one(cast(Bot, _bot(error="temporary failure")), factory, clock=clock)
                is True
            )
        elif queued_status is OrderReminderStatus.SENDING:
            async with factory.begin() as session:
                await session.execute(
                    update(OrderReminder)
                    .where(OrderReminder.id == old_rows[0].id)
                    .values(status=queued_status.value)
                )
        assert (await _ledger(factory, int(order.id)))[0].status == queued_status.value
        before_events = await _events(factory)

        assert await _service(factory, clock).reschedule(
            user,
            int(order.id),
            deadline_utc=NOW + timedelta(hours=120),
            lead_time_minutes=24 * 60,
            warning_count=3,
        ) == (3, 0)
        rows = await _ledger(factory, int(order.id))
        assert len(rows) == 8
        assert all(row.status == OrderReminderStatus.SUPERSEDED.value for row in rows[:5])
        assert all(row.status == OrderReminderStatus.PENDING.value for row in rows[5:])
        after_events = await _events(factory)
        assert [(event.id, event.status, event.attempt_count) for event in after_events] == [
            (event.id, event.status, event.attempt_count) for event in before_events
        ]

        await _queue_is_eligible(factory)
        bot = _bot()
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is False
        assert cast(AsyncMock, bot.send_message).await_count == 0
        assert all(
            event.status == OutboxEventStatus.CANCELLED.value for event in await _events(factory)
        )
        rows = await _ledger(factory, int(order.id))
        assert all(row.status == OrderReminderStatus.SUPERSEDED.value for row in rows[:5])
        assert await _delivery(factory, clock).claim_due() == 0

    _scenario(scenario)


@pytest.mark.parametrize("warning_count", [5, 10])
def test_unchanged_or_overlapping_reschedule_reuses_only_future_instants(
    warning_count: int,
) -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        old_rows = await _ledger(factory, int(order.id))
        # Equality is stale too: the second instant must not reopen on reschedule.
        clock.now = old_rows[1].scheduled_for
        assert await _delivery(factory, clock).claim_due() == 2
        async with factory.begin() as session:
            # A reused row must discard any lease timestamp left by an earlier claim.
            await session.execute(
                update(OrderReminder)
                .where(OrderReminder.id.in_([row.id for row in old_rows[2:]]))
                .values(claimed_at=NOW)
            )
        service = _service(factory, clock)
        expected = (3, 0) if warning_count == 5 else (7, 1)
        result = await service.reschedule(user, int(order.id), warning_count=warning_count)
        assert result == expected
        rows = await _ledger(factory, int(order.id))
        assert len(rows) == warning_count
        by_id = {row.id: row for row in rows}
        assert all(
            by_id[row.id].status == OrderReminderStatus.SUPERSEDED.value for row in old_rows[:2]
        )
        for old in old_rows[2:]:
            reused = by_id[old.id]
            assert reused.status == OrderReminderStatus.PENDING.value
            assert reused.ordinal == old.ordinal * (warning_count // 5)
            assert reused.claimed_at is None
            assert reused.idempotency_key == old.idempotency_key
        future = [row for row in rows if row.scheduled_for > clock.now]
        assert len(future) == expected[0]
        assert all(row.status == OrderReminderStatus.PENDING.value for row in future)

        # Repeating the same edit neither duplicates nor loses an overlapping warning.
        assert await service.reschedule(user, int(order.id), warning_count=warning_count) == (
            expected[0],
            0,
        )
        assert [row.id for row in await _ledger(factory, int(order.id))] == [row.id for row in rows]
        await _queue_is_eligible(factory)
        bot = _bot()
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
        assert cast(AsyncMock, bot.send_message).await_count == 0
        assert all(
            event.status == OutboxEventStatus.CANCELLED.value for event in await _events(factory)
        )

    _scenario(scenario)


@pytest.mark.parametrize("overlapping", [False, True])
def test_frozen_reschedule_keeps_new_and_reused_instants_cancelled_until_resume(
    overlapping: bool,
) -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        service = _service(factory, clock)
        old_rows = await _ledger(factory, int(order.id))
        await service.act(user, int(order.id), workflow.OrderAction.FREEZE)
        clock.now = NOW + timedelta(hours=40)
        deadline = DEADLINE if overlapping else NOW + timedelta(hours=120)
        lead_minutes = 48 * 60 if overlapping else 24 * 60
        count = 10 if overlapping else 3
        instants = schedule.review_instants(
            deadline_utc=deadline, lead_time_minutes=lead_minutes, warning_count=count
        )
        expected = (6, 2) if overlapping else (3, 0)
        assert (
            await service.reschedule(
                user,
                int(order.id),
                deadline_utc=deadline,
                lead_time_minutes=lead_minutes,
                warning_count=count,
            )
            == expected
        )
        rows = await _ledger(factory, int(order.id))
        by_instant = {row.scheduled_for: row for row in rows}
        for old in old_rows:
            if old.scheduled_for <= clock.now or old.scheduled_for not in instants:
                assert by_instant[old.scheduled_for].status == OrderReminderStatus.SUPERSEDED.value
        for ordinal, instant in enumerate(instants):
            row = by_instant[instant]
            if instant > clock.now:
                assert row.status == OrderReminderStatus.CANCELLED.value
                assert row.ordinal == ordinal
                assert row.claimed_at is None
        assert sum(row.status == OrderReminderStatus.SKIPPED.value for row in rows) == expected[1]
        assert all(row.status != OrderReminderStatus.PENDING.value for row in rows)
        assert (await service.detail(user, int(order.id))).order.state == OrderState.FROZEN.value
        assert await _delivery(factory, clock).claim_due() == 0
        assert await _events(factory) == []

        # Let one new instant become due while frozen. Resume must not replay it.
        clock.now = min(instant for instant in instants if instant > clock.now)
        before = {row.id: row.status for row in rows if row.scheduled_for <= clock.now}
        await service.act(user, int(order.id), workflow.OrderAction.RESUME)
        rows = await _ledger(factory, int(order.id))
        for row in rows:
            if row.id in before:
                assert row.status == before[row.id]
            elif row.scheduled_for in instants:
                assert row.status == OrderReminderStatus.PENDING.value
                assert row.claimed_at is None
            else:
                assert row.status == OrderReminderStatus.SUPERSEDED.value
        pending = sum(row.status == OrderReminderStatus.PENDING.value for row in rows)
        assert pending == expected[0] - 1
        assert await _delivery(factory, clock).claim_due() == 0
        assert await _events(factory) == []

    _scenario(scenario)


def test_reschedule_freeze_and_resume_preserve_sent_failed_and_skipped_history() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        rows = await _ledger(factory, int(order.id))
        terminal = [
            OrderReminderStatus.SENT,
            OrderReminderStatus.FAILED,
            OrderReminderStatus.SKIPPED,
        ]
        async with factory.begin() as session:
            for row, status in zip(rows[:3], terminal, strict=True):
                await session.execute(
                    update(OrderReminder)
                    .where(OrderReminder.id == row.id)
                    .values(
                        status=status.value,
                        claimed_at=NOW,
                        sent_at=NOW if status is OrderReminderStatus.SENT else None,
                        attempt_count=1,
                        last_error=(
                            "preserved history" if status is OrderReminderStatus.FAILED else None
                        ),
                    )
                )
        before = await _ledger(factory, int(order.id))
        service = _service(factory, clock)
        # All old instants overlap, but terminal rows must never reopen or change ordinal.
        assert await service.reschedule(user, int(order.id), warning_count=10) == (7, 0)
        await service.act(user, int(order.id), workflow.OrderAction.FREEZE)
        await service.act(user, int(order.id), workflow.OrderAction.RESUME)
        after = {row.id: row for row in await _ledger(factory, int(order.id))}
        for old in before[:3]:
            current = after[old.id]
            assert current.status == old.status
            assert current.ordinal == old.ordinal
            assert current.claimed_at == old.claimed_at
            assert current.sent_at == old.sent_at
            assert current.attempt_count == old.attempt_count
            assert current.last_error == old.last_error
        assert sum(row.status == OrderReminderStatus.PENDING.value for row in after.values()) == 7

    _scenario(scenario)


def test_delete_suppresses_queued_claims_and_retries_without_erasing_the_ledger() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        clock.now = DUE_AT
        assert await _delivery(factory, clock).claim_due() == 2
        await _queue_is_eligible(factory)
        assert (
            await _send_one(cast(Bot, _bot(error="temporary failure")), factory, clock=clock)
            is True
        )
        rows = await _ledger(factory, int(order.id))
        assert rows[0].status == OrderReminderStatus.RETRY_PENDING.value
        assert rows[1].status == OrderReminderStatus.CLAIMED.value

        deleted = await _service(factory, clock).act(
            user, int(order.id), workflow.OrderAction.DELETE
        )
        assert deleted.state == OrderState.DELETED.value
        stopped = await _ledger(factory, int(order.id))
        assert [row.id for row in stopped] == [row.id for row in rows]
        assert all(row.status == OrderReminderStatus.CANCELLED.value for row in stopped)
        assert all(
            event.status == OutboxEventStatus.PENDING.value for event in await _events(factory)
        )
        await _queue_is_eligible(factory)
        bot = _bot()
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is True
        assert await _send_one(cast(Bot, bot), factory, clock=clock) is False
        assert cast(AsyncMock, bot.send_message).await_count == 0
        assert all(
            event.status == OutboxEventStatus.CANCELLED.value for event in await _events(factory)
        )
        clock.now = DEADLINE
        assert await _delivery(factory, clock).claim_due() == 0
        assert all(
            row.status == OrderReminderStatus.CANCELLED.value
            for row in await _ledger(factory, int(order.id))
        )

    _scenario(scenario)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (Forbidden("blocked"), "Forbidden"),
        (BadRequest("chat not found"), "BadRequest"),
        (TimedOut("synthetic sensitive input"), "DELIVERY_UNKNOWN"),
    ],
)
def test_terminal_or_uncertain_provider_outcomes_are_not_replayed(
    error: TelegramError,
    code: str,
) -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        clock.now = NOW + timedelta(hours=24)
        assert await _delivery(factory, clock).claim_due() == 1
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=error))
        assert await _send_one(cast(Bot, bot), factory, clock=clock)
        event = (await _events(factory))[0]
        row = (await _ledger(factory, int(order.id)))[0]
        assert event.status == OutboxEventStatus.FAILED.value
        assert row.status == OrderReminderStatus.FAILED.value
        assert row.sent_at is None and event.sent_at is None
        assert row.last_error == event.last_error == code
        assert row.attempt_count == event.attempt_count == 1
        assert not await _send_one(cast(Bot, bot), factory, clock=clock)
        assert cast(AsyncMock, bot.send_message).await_count == 1
        history = (await _service(factory, clock).detail(user, int(order.id))).history
        assert any(item.action == "ORDER_REMINDER_STARTED" for item in history)
        outcome = "UNKNOWN" if code == "DELIVERY_UNKNOWN" else "FAILED"
        assert history[0].action == f"ORDER_REMINDER_{outcome}"
        assert history[0].event_metadata is not None
        assert history[0].event_metadata["error_code"] == code
        assert "synthetic sensitive input" not in str([item.event_metadata for item in history])

    _scenario(scenario)


def test_rate_limit_honors_delay_and_success_preserves_both_attempts() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        clock.now = NOW + timedelta(hours=24)
        assert await _delivery(factory, clock).claim_due() == 1
        bot = SimpleNamespace(
            send_message=AsyncMock(side_effect=[RetryAfter(90), SimpleNamespace(message_id=987)])
        )
        assert await _send_one(cast(Bot, bot), factory, clock=clock)
        assert (await _events(factory))[0].available_at == clock.now + timedelta(seconds=90)
        assert not await _send_one(cast(Bot, bot), factory, clock=clock)
        clock.now += timedelta(seconds=89)
        assert not await _send_one(cast(Bot, bot), factory, clock=clock)
        clock.now += timedelta(seconds=1)
        assert await _send_one(cast(Bot, bot), factory, clock=clock)
        row = (await _ledger(factory, int(order.id)))[0]
        assert row.status == OrderReminderStatus.SENT.value
        assert row.attempt_count == 2 and row.last_error is None
        history = (await _service(factory, clock).detail(user, int(order.id))).history
        attempts = [item for item in history if item.action.startswith("ORDER_REMINDER_")]
        assert [item.action for item in attempts] == [
            "ORDER_REMINDER_SENT",
            "ORDER_REMINDER_STARTED",
            "ORDER_REMINDER_FAILED",
            "ORDER_REMINDER_STARTED",
        ]
        assert attempts[0].event_metadata is not None
        assert attempts[0].event_metadata["telegram_message_id"] == 987
        assert attempts[0].event_metadata["attempt"] == 2

    _scenario(scenario)


def test_post_send_transaction_crash_never_replays_an_uncertain_message() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        clock.now = NOW + timedelta(hours=24)
        assert await _delivery(factory, clock).claim_due() == 1
        bot = _bot()
        original = order_delivery.record_attempt

        def crash_on_success(
            session: AsyncSession,
            entry: OrderReminder,
            event: OutboxEvent,
            outcome: str,
            *,
            message_id: int | None = None,
        ) -> None:
            if outcome == "SENT":
                raise RuntimeError("simulated process death after Telegram accepted")
            original(session, entry, event, outcome, message_id=message_id)

        with patch.object(order_delivery, "record_attempt", side_effect=crash_on_success):
            with pytest.raises(RuntimeError, match="simulated process death"):
                await _send_one(cast(Bot, bot), factory, clock=clock)
        assert cast(AsyncMock, bot.send_message).await_count == 1
        assert (await _events(factory))[0].status == OutboxEventStatus.SENDING.value
        assert (await _ledger(factory, int(order.id)))[
            0
        ].status == OrderReminderStatus.SENDING.value
        # Restart before lease expiry cannot claim or send this reservation.
        assert not await _send_one(cast(Bot, bot), factory, clock=clock)
        assert await _delivery(factory, clock).claim_due() == 0
        clock.now += timedelta(minutes=6)
        assert await _delivery(factory, clock).reclaim_stale() == 1
        event = (await _events(factory))[0]
        assert event.status == OutboxEventStatus.FAILED.value
        assert event.last_error == "DELIVERY_UNKNOWN" and event.sent_at is None
        assert not await _send_one(cast(Bot, bot), factory, clock=clock)
        assert await _delivery(factory, clock).reclaim_stale() == 0
        assert cast(AsyncMock, bot.send_message).await_count == 1
        history = (await _service(factory, clock).detail(user, int(order.id))).history
        assert history[0].action == "ORDER_REMINDER_UNKNOWN"

    _scenario(scenario)


def test_stop_between_durable_reservation_and_network_call_prevents_send() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        user, order = await _confirmed(factory, clock)
        clock.now = NOW + timedelta(hours=24)
        await _delivery(factory, clock).claim_due()
        event_id = int((await _events(factory))[0].id)
        async with factory.begin() as session:
            event = await session.scalar(
                select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update()
            )
            assert event is not None
            assert await order_delivery.reserve_order_send(session, event, clock)
        service = _service(factory, clock)
        await service.act(user, int(order.id), workflow.OrderAction.FREEZE)
        await service.act(user, int(order.id), workflow.OrderAction.RESUME)
        bot = _bot()
        await order_delivery.deliver_reserved_order(cast(Bot, bot), factory, event_id, clock)
        assert cast(AsyncMock, bot.send_message).await_count == 0
        assert (await _events(factory))[0].status == OutboxEventStatus.CANCELLED.value

    _scenario(scenario)


def test_two_consumers_cannot_send_one_reserved_event_twice() -> None:
    async def scenario(factory: Factory, clock: FakeClock) -> None:
        _, order = await _confirmed(factory, clock)
        clock.now = NOW + timedelta(hours=24)
        assert await _delivery(factory, clock).claim_due() == 1
        bot = _bot()
        await asyncio.gather(
            _send_one(cast(Bot, bot), factory, clock=clock),
            _send_one(cast(Bot, bot), factory, clock=clock),
        )
        assert cast(AsyncMock, bot.send_message).await_count == 1
        assert (await _ledger(factory, int(order.id)))[0].attempt_count == 1
        assert (await _events(factory))[0].status == OutboxEventStatus.SENT.value

    _scenario(scenario)
