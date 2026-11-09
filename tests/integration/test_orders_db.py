"""Real PostgreSQL evidence for the Orders domain (owner contract V2 §3).

Mocks cannot prove the parts that matter here: a draft that survives a restart, one
ledger row per scheduled instant, freezing that stops what has not been sent, resuming
that reopens only what is still ahead, and the per-user boundary. Those are PostgreSQL
row locks, unique constraints and cascades, so the tests run against the disposable CI
database with a fake clock.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_common import FeatureAccessDenied
from office_assistant.application.errors import (
    AuthorizationDenied,
    EntityNotFound,
    InvalidBusinessValue,
)
from office_assistant.application.orders import OrderFilter, OrderIntakeError, OrderService
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
    User,
    UserFeaturePermission,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.orders import schedule, workflow
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
