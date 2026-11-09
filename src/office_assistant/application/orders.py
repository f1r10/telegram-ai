"""Orders application service: durable drafts, confirmed workflow and review schedule.

Owner contract V2 §3 (docs/V2-OWNER-REQUIREMENTS-2026-11-08.md). Every rule that decides
a number, a state or a scheduled instant lives here or in :mod:`office_assistant.orders`;
the Telegram adapter only turns buttons and text into typed calls. The draft is a
PostgreSQL row, so intake survives a restart, and nothing in this service reads or
writes inventory.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_common import ensure_feature
from office_assistant.application.errors import (
    ApplicationError,
    AuthorizationDenied,
    EntityNotFound,
    InvalidBusinessValue,
    InvalidStateTransition,
)
from office_assistant.auth.features import FeatureKey
from office_assistant.core.clock import Clock
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    FileObject,
    Order,
    OrderAttachment,
    OrderReminder,
    OrderReminderStatus,
    OrderState,
    User,
)
from office_assistant.orders import schedule, workflow
from office_assistant.repositories.audit import AuditRepository

MAX_TEXT_LENGTH = 4000
LIST_LIMIT = 50
HISTORY_LIMIT = 20
# ASSUMPTION (docs/OPEN-QUESTIONS.md Q47): a one-year lead window is the widest plan an
# operator can ask for; anything larger reads as a typo and gets a clarification instead.
MAX_LEAD_MINUTES = 60 * 24 * 365


class OrderFilter(StrEnum):
    """The views of **Sifarişlərim**; a deleted order appears in none of them."""

    OPEN = "OPEN"
    OVERDUE = "OVERDUE"
    FROZEN = "FROZEN"
    ARCHIVED = "ARCHIVED"
    DRAFTS = "DRAFTS"


class OrderIntakeError(ApplicationError):
    """The draft cannot move on yet; the adapter asks for this field again."""

    def __init__(self, field: str) -> None:
        super().__init__(f"the order draft still needs: {field}")
        self.field = field


@dataclass(frozen=True, slots=True)
class OrderWarning:
    """One planned review instant, exactly as the order view shows it."""

    ordinal: int
    scheduled_for: datetime
    status: str


@dataclass(frozen=True, slots=True)
class OrderMaterial:
    """An attached upload with enough provenance to hand the original back in Telegram."""

    file_object_id: int
    original_filename: str
    content_type: str
    position: int


@dataclass(frozen=True, slots=True)
class OrderView:
    """Everything the detail page and the confirmation review need, in one read."""

    order: Order
    warnings: tuple[OrderWarning, ...]
    materials: tuple[OrderMaterial, ...]
    history: tuple[AuditEvent, ...]
    is_overdue: bool
    remaining: timedelta | None

    @property
    def planned(self) -> int:
        live = {
            OrderReminderStatus.PENDING.value,
            OrderReminderStatus.CLAIMED.value,
            OrderReminderStatus.SENDING.value,
            OrderReminderStatus.RETRY_PENDING.value,
        }
        return sum(1 for warning in self.warnings if warning.status in live)

    @property
    def delivered(self) -> int:
        return sum(1 for warning in self.warnings if warning.status == OrderReminderStatus.SENT)

    @property
    def skipped(self) -> int:
        """Warnings that will never arrive: stale when planned, stopped, or replaced."""

        stopped = {
            OrderReminderStatus.SKIPPED.value,
            OrderReminderStatus.CANCELLED.value,
            OrderReminderStatus.SUPERSEDED.value,
        }
        return sum(1 for warning in self.warnings if warning.status in stopped)

    @property
    def failed(self) -> int:
        return sum(1 for warning in self.warnings if warning.status == OrderReminderStatus.FAILED)


class OrderService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, clock: Clock) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def start(self, user: User) -> Order:
        """Open a draft, or return the one this user is already answering.

        One open draft per account is a database invariant, so an interrupted intake
        resumes into the same row instead of mixing two orders together.
        """

        async with self._session_factory.begin() as session:
            await ensure_feature(session, user, FeatureKey.ORDERS)
            open_draft = await self._open_draft(session, user)
            if open_draft is not None:
                return open_draft
            draft = Order(
                created_by_user_id=user.id,
                intake_token=_token(),
                description="",
                timezone=user.timezone,
                state=OrderState.PENDING_CONFIRMATION.value,
            )
            session.add(draft)
            await session.flush()
            AuditRepository(session).add(
                actor_user_id=user.id,
                action="ORDER_DRAFT_CREATED",
                entity_type="order",
                entity_id=str(draft.id),
            )
            return draft

    async def current_draft(self, user: User) -> Order | None:
        """The draft this user is answering, so a restart resumes instead of restarting."""

        async with self._session_factory() as session:
            await ensure_feature(session, user, FeatureKey.ORDERS)
            return await self._open_draft(session, user)

    async def next_field(self, user: User, token: str) -> str:
        """The intake question to ask now; raises when the draft is ready to review."""

        async with self._session_factory() as session:
            order = await self._draft_for_read(session, user, token)
            missing = workflow.next_open_field(_intake_values(order))
            if missing is None:
                raise InvalidStateTransition("the draft is complete, review it instead")
            return missing

    async def answer(self, user: User, token: str, field: str, value: object) -> Order:
        """Record one intake answer. It lives in PostgreSQL before the next question."""

        async with self._session_factory.begin() as session:
            order = await self._draft_for_update(session, user, token)
            _apply_answer(order, field, value, now=self._clock.now_utc())
            AuditRepository(session).add(
                actor_user_id=user.id,
                action="ORDER_INTAKE_ANSWERED",
                entity_type="order",
                entity_id=str(order.id),
                metadata={"field": field},
            )
            return order

    async def review(self, user: User, token: str) -> OrderView:
        """The complete review page shown before the authoritative confirmation."""

        async with self._session_factory() as session:
            order = await self._draft_for_read(session, user, token)
            values = _intake_values(order)
            if not workflow.is_complete(values):
                raise OrderIntakeError(workflow.next_open_field(values) or "description")
            return await self._view(session, order)

    async def attach(
        self,
        user: User,
        token: str,
        file_object_id: int,
        *,
        telegram_message_id: int | None = None,
    ) -> OrderAttachment:
        """Reference an already stored upload; the bytes stay owned by ``file_objects``."""

        async with self._session_factory.begin() as session:
            order = await self._draft_for_update(session, user, token)
            file_object = await session.get(FileObject, file_object_id)
            if file_object is None:
                raise EntityNotFound("File object does not exist")
            position = (
                await session.scalar(
                    select(func.count())
                    .select_from(OrderAttachment)
                    .where(OrderAttachment.order_id == order.id)
                )
                or 0
            ) + 1
            attachment = OrderAttachment(
                order_id=int(order.id),
                file_object_id=file_object_id,
                added_by_user_id=user.id,
                telegram_message_id=telegram_message_id,
                position=int(position),
            )
            session.add(attachment)
            await session.flush()
            AuditRepository(session).add(
                actor_user_id=user.id,
                action="ORDER_ATTACHMENT_ADDED",
                entity_type="order",
                entity_id=str(order.id),
                metadata={"position": int(position), "file_object_id": file_object_id},
            )
            return attachment

    async def confirm(self, user: User, token: str) -> Order:
        """Promote a complete draft to ACTIVE and write its review schedule once.

        Idempotent: a retried confirmation finds the same row by its still-present intake
        token, sees a state after ``PENDING_CONFIRMATION`` and returns it untouched. The
        plan is keyed by order plus instant, so neither a double tap nor a worker replay
        can add a second warning for the same moment.
        """

        now = self._clock.now_utc()
        async with self._session_factory.begin() as session:
            order = await self._draft_by_token(session, user, token, for_update=True)
            if OrderState(order.state) is not OrderState.PENDING_CONFIRMATION:
                return order
            values = _intake_values(order)
            if not workflow.is_complete(values):
                raise OrderIntakeError(workflow.next_open_field(values) or "description")
            assert order.deadline_utc is not None
            if order.deadline_utc <= now:
                raise InvalidBusinessValue("the order deadline is already in the past")
            scheduled, skipped = await self._plan(session, order, now)
            order.state = workflow.next_state(order.state, workflow.OrderAction.CONFIRM).value
            order.confirmed_at = now
            AuditRepository(session).add(
                actor_user_id=user.id,
                action="ORDER_CONFIRMED",
                entity_type="order",
                entity_id=str(order.id),
                metadata={
                    "scheduled": scheduled,
                    "skipped": skipped,
                    "deadline_utc": order.deadline_utc.isoformat(),
                    "lead_time_minutes": order.lead_time_minutes,
                    "warning_count": order.warning_count,
                    "timezone": order.timezone,
                },
            )
            return order

    async def abandon(self, user: User, token: str) -> Order:
        """Ləğv et on an unfinished draft; nothing authoritative was ever written."""

        now = self._clock.now_utc()
        async with self._session_factory.begin() as session:
            order = await self._draft_by_token(session, user, token, for_update=True)
            if OrderState(order.state) is not OrderState.PENDING_CONFIRMATION:
                return order
            order.state = workflow.next_state(order.state, workflow.OrderAction.CANCEL).value
            order.cancelled_at = now
            AuditRepository(session).add(
                actor_user_id=user.id,
                action="ORDER_CANCELLED",
                entity_type="order",
                entity_id=str(order.id),
                metadata={"stage": "intake"},
            )
            return order

    async def act(self, user: User, order_id: int, action: workflow.OrderAction) -> Order:
        """Bitir / Dondur / Davam etdir / Ləğv et / Sil, each with its ledger effect."""

        now = self._clock.now_utc()
        async with self._session_factory.begin() as session:
            order = await self._order_for_update(session, user, order_id)
            target = workflow.next_state(order.state, action)
            stamp = workflow.stamp_field(action)
            if stamp is not None:
                setattr(order, stamp, now)
            order.state = target.value
            stopped = 0
            scheduled = 0
            if action is workflow.OrderAction.RESUME:
                scheduled, _ = await self._plan(session, order, now, restore=True)
            elif action is not workflow.OrderAction.CONFIRM:
                stopped = await self._stop_pending(session, int(order.id))
            AuditRepository(session).add(
                actor_user_id=user.id,
                action=f"ORDER_{action.value}",
                entity_type="order",
                entity_id=str(order.id),
                metadata={"state": target.value, "stopped": stopped, "scheduled": scheduled},
            )
            return order

    async def reschedule(
        self,
        user: User,
        order_id: int,
        *,
        deadline_utc: datetime | None = None,
        lead_time_minutes: int | None = None,
        warning_count: int | None = None,
    ) -> tuple[int, int]:
        """Change the plan without losing the warnings that already happened.

        Pending entries of the old plan are superseded, never deleted, and the new plan
        is computed against the new deadline. Returns ``(scheduled, skipped)``.
        """

        now = self._clock.now_utc()
        async with self._session_factory.begin() as session:
            order = await self._order_for_update(session, user, order_id)
            if OrderState(order.state) not in {OrderState.ACTIVE, OrderState.FROZEN}:
                raise InvalidStateTransition("only a running or frozen order can be rescheduled")
            if deadline_utc is not None:
                order.deadline_utc = _as_utc(deadline_utc)
            if lead_time_minutes is not None:
                order.lead_time_minutes = _lead_minutes(lead_time_minutes)
            if warning_count is not None:
                order.warning_count = _warning_count(warning_count)
            assert order.deadline_utc is not None
            if order.deadline_utc <= now:
                raise InvalidBusinessValue("the order deadline is already in the past")
            superseded = await self._supersede_pending(session, int(order.id), now)
            scheduled, skipped = await self._plan(session, order, now)
            AuditRepository(session).add(
                actor_user_id=user.id,
                action="ORDER_RESCHEDULED",
                entity_type="order",
                entity_id=str(order.id),
                metadata={"superseded": superseded, "scheduled": scheduled, "skipped": skipped},
            )
            return scheduled, skipped

    async def list(self, user: User, view: OrderFilter) -> list[Order]:
        now = self._clock.now_utc()
        async with self._session_factory() as session:
            await ensure_feature(session, user, FeatureKey.ORDERS)
            statement = select(Order).where(
                Order.created_by_user_id == user.id, Order.state != OrderState.DELETED.value
            )
            if view is OrderFilter.OPEN:
                statement = statement.where(
                    Order.state.in_([OrderState.ACTIVE.value, OrderState.FROZEN.value])
                ).order_by(Order.deadline_utc)
            elif view is OrderFilter.OVERDUE:
                statement = statement.where(
                    Order.state == OrderState.ACTIVE.value, Order.deadline_utc < now
                ).order_by(Order.deadline_utc)
            elif view is OrderFilter.FROZEN:
                statement = statement.where(Order.state == OrderState.FROZEN.value).order_by(
                    Order.deadline_utc
                )
            elif view is OrderFilter.ARCHIVED:
                statement = statement.where(
                    Order.state.in_([OrderState.COMPLETED.value, OrderState.CANCELLED.value])
                ).order_by(Order.updated_at.desc())
            else:
                statement = statement.where(
                    Order.state == OrderState.PENDING_CONFIRMATION.value
                ).order_by(Order.created_at.desc())
            rows = (await session.execute(statement.limit(LIST_LIMIT))).scalars()
            return list(rows.all())

    async def detail(self, user: User, order_id: int) -> OrderView:
        async with self._session_factory() as session:
            await ensure_feature(session, user, FeatureKey.ORDERS)
            order = await session.scalar(
                select(Order).where(Order.id == order_id, Order.created_by_user_id == user.id)
            )
            if order is None or OrderState(order.state) is OrderState.DELETED:
                raise EntityNotFound("Order does not exist")
            return await self._view(session, order)

    async def _view(self, session: AsyncSession, order: Order) -> OrderView:
        now = self._clock.now_utc()
        warnings = (
            (
                await session.execute(
                    select(OrderReminder)
                    .where(OrderReminder.order_id == order.id)
                    .order_by(OrderReminder.scheduled_for)
                )
            )
            .scalars()
            .all()
        )
        material_rows = (
            await session.execute(
                select(
                    OrderAttachment.file_object_id,
                    FileObject.original_filename,
                    FileObject.content_type,
                    OrderAttachment.position,
                )
                .join(FileObject, FileObject.id == OrderAttachment.file_object_id)
                .where(OrderAttachment.order_id == order.id)
                .order_by(OrderAttachment.position)
            )
        ).all()
        history = (
            (
                await session.execute(
                    select(AuditEvent)
                    .where(AuditEvent.entity_type == "order", AuditEvent.entity_id == str(order.id))
                    .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                    .limit(HISTORY_LIMIT)
                )
            )
            .scalars()
            .all()
        )
        return OrderView(
            order=order,
            warnings=tuple(
                OrderWarning(
                    ordinal=row.ordinal, scheduled_for=row.scheduled_for, status=row.status
                )
                for row in warnings
            ),
            materials=tuple(
                OrderMaterial(
                    file_object_id=int(item[0]),
                    original_filename=str(item[1]),
                    content_type=str(item[2]),
                    position=int(item[3]),
                )
                for item in material_rows
            ),
            history=tuple(history),
            is_overdue=workflow.is_overdue(order.state, order.deadline_utc, now),
            remaining=None if order.deadline_utc is None else order.deadline_utc - now,
        )

    async def _open_draft(self, session: AsyncSession, user: User) -> Order | None:
        return await session.scalar(
            select(Order)
            .where(
                Order.created_by_user_id == user.id,
                Order.state == OrderState.PENDING_CONFIRMATION.value,
            )
            .order_by(Order.created_at)
        )

    async def _draft_for_read(self, session: AsyncSession, user: User, token: str) -> Order:
        return await self._checked_draft(session, user, token, for_update=False)

    async def _draft_for_update(self, session: AsyncSession, user: User, token: str) -> Order:
        return await self._checked_draft(session, user, token, for_update=True)

    async def _checked_draft(
        self, session: AsyncSession, user: User, token: str, *, for_update: bool
    ) -> Order:
        order = await self._draft_by_token(session, user, token, for_update=for_update)
        if OrderState(order.state) is not OrderState.PENDING_CONFIRMATION:
            raise InvalidStateTransition("this draft is no longer open for intake")
        return order

    async def _draft_by_token(
        self, session: AsyncSession, user: User, token: str, *, for_update: bool
    ) -> Order:
        await ensure_feature(session, user, FeatureKey.ORDERS)
        statement = select(Order).where(Order.intake_token == token)
        if for_update:
            statement = statement.with_for_update()
        order = await session.scalar(statement)
        if order is None:
            raise EntityNotFound("Order draft does not exist")
        if order.created_by_user_id != user.id:
            raise AuthorizationDenied("Order drafts belong to their creator")
        return order

    async def _order_for_update(self, session: AsyncSession, user: User, order_id: int) -> Order:
        await ensure_feature(session, user, FeatureKey.ORDERS)
        order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if order is None:
            raise EntityNotFound("Order does not exist")
        if order.created_by_user_id != user.id:
            raise AuthorizationDenied("Order belongs to another user")
        return order

    async def _plan(
        self, session: AsyncSession, order: Order, now: datetime, *, restore: bool = False
    ) -> tuple[int, int]:
        """Write the plan for the current deadline: future instants only, once each.

        The idempotency key is the uniqueness guard, so a retry, a restart or a
        concurrent claim cannot add a second warning for the same instant. An instant
        already in the past is stored as ``SKIPPED`` instead of queued, which is what the
        order view discloses rather than sending a stale burst. Under ``restore`` (resuming
        a frozen order) the entries freezing cancelled are reopened only while they are
        still ahead of now, so resuming never replays a reminder.
        """

        if (
            order.deadline_utc is None
            or order.lead_time_minutes is None
            or order.warning_count is None
        ):
            return 0, 0
        instants = schedule.review_instants(
            deadline_utc=order.deadline_utc,
            lead_time_minutes=order.lead_time_minutes,
            warning_count=order.warning_count,
        )
        scheduled = 0
        skipped = 0
        for ordinal, instant in enumerate(instants):
            key = schedule.idempotency_key(order_id=int(order.id), scheduled_for=instant)
            existing = await session.scalar(
                select(OrderReminder).where(OrderReminder.idempotency_key == key).with_for_update()
            )
            if instant <= now:
                if existing is None:
                    session.add(
                        OrderReminder(
                            order_id=int(order.id),
                            ordinal=ordinal,
                            scheduled_for=instant,
                            status=OrderReminderStatus.SKIPPED.value,
                            idempotency_key=key,
                        )
                    )
                    skipped += 1
                continue
            if existing is None:
                session.add(
                    OrderReminder(
                        order_id=int(order.id),
                        ordinal=ordinal,
                        scheduled_for=instant,
                        status=OrderReminderStatus.PENDING.value,
                        idempotency_key=key,
                    )
                )
                scheduled += 1
            elif restore and existing.status == OrderReminderStatus.CANCELLED.value:
                existing.status = OrderReminderStatus.PENDING.value
                existing.ordinal = ordinal
                existing.claimed_at = None
                scheduled += 1
        await session.flush()
        return scheduled, skipped

    async def _stop_pending(self, session: AsyncSession, order_id: int) -> int:
        """Freeze, finish, cancel or delete: stop everything not yet delivered."""

        rows = (
            (
                await session.execute(
                    select(OrderReminder)
                    .where(
                        OrderReminder.order_id == order_id,
                        OrderReminder.status.in_(
                            [OrderReminderStatus.PENDING.value, OrderReminderStatus.RETRY_PENDING]
                        ),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for entry in rows:
            entry.status = OrderReminderStatus.CANCELLED.value
        return len(rows)

    async def _supersede_pending(self, session: AsyncSession, order_id: int, now: datetime) -> int:
        rows = (
            (
                await session.execute(
                    select(OrderReminder)
                    .where(
                        OrderReminder.order_id == order_id,
                        OrderReminder.status == OrderReminderStatus.PENDING.value,
                        OrderReminder.scheduled_for > now,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for entry in rows:
            entry.status = OrderReminderStatus.SUPERSEDED.value
        return len(rows)


def _intake_values(order: Order) -> dict[str, object]:
    return {
        "deadline": order.deadline_utc,
        "lead_time": order.lead_time_minutes,
        "warnings": order.warning_count,
        "requester": order.requested_by,
        "description": order.description,
        "notes": order.notes,
    }


def _apply_answer(order: Order, field: str, value: object, *, now: datetime) -> None:
    """Validate one answer and store it, or raise the clarification the user needs."""

    if field == "deadline":
        if not isinstance(value, datetime):
            raise InvalidBusinessValue("deadline must be a date and time")
        deadline = _as_utc(value)
        if deadline <= now:
            raise InvalidBusinessValue("the deadline must be in the future")
        order.deadline_utc = deadline
    elif field == "lead_time":
        order.lead_time_minutes = _lead_minutes(value)
    elif field == "warnings":
        order.warning_count = _warning_count(value)
    elif field == "requester":
        order.requested_by = _text(value, "requester")
    elif field == "description":
        order.description = _text(value, "description")
    elif field == "notes":
        order.notes = None if value is None else _text(value, "notes")
    else:
        raise InvalidBusinessValue(f"unknown intake field {field}")


def _lead_minutes(value: object) -> int:
    minutes = _whole_number(value, "lead time in minutes")
    if not 1 <= minutes <= MAX_LEAD_MINUTES:
        raise InvalidBusinessValue(f"lead time must be between 1 and {MAX_LEAD_MINUTES} minutes")
    return minutes


def _warning_count(value: object) -> int:
    count = _whole_number(value, "warning count")
    if not schedule.MIN_WARNINGS <= count <= schedule.MAX_WARNINGS:
        raise InvalidBusinessValue(
            f"warning count must be between {schedule.MIN_WARNINGS} and {schedule.MAX_WARNINGS}"
        )
    return count


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise InvalidBusinessValue("the deadline must carry a timezone")
    return value.astimezone(UTC)


def _whole_number(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidBusinessValue(f"{label} must be a whole number")
    if value < 0:
        raise InvalidBusinessValue(f"{label} cannot be negative")
    return int(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidBusinessValue(f"{label} must be a non-empty text")
    return value.strip()[:MAX_TEXT_LENGTH]


def _token() -> str:
    return uuid4().hex[:16]
