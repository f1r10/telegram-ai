"""Reminder application service: proposal confirmation flow and CRUD.

Implements the SPEC §5 rule that nothing is saved until the user confirms the
bot's interpretation (Confirm/Edit/Cancel). Pending proposals live in a bounded
in-process store; confirmed reminders live in PostgreSQL only.
"""

from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.core.clock import Clock
from office_assistant.infrastructure.database.models import (
    AIConversation,
    IngestionSession,
    Reminder,
    ReminderStatus,
    User,
)
from office_assistant.reminders.time_parsing import parse_reminder_time

PROPOSAL_TTL_SECONDS = 1800.0
MAX_OPEN_PROPOSALS = 1000


@dataclass(frozen=True, slots=True)
class ReminderProposal:
    """A parsed, user-addressable interpretation awaiting confirmation."""

    token: str
    owner_user_id: int
    reminder_text: str
    due_at_utc: datetime
    timezone_name: str
    needs_clarification: bool
    clarification_reason: str | None


@dataclass(slots=True)
class _PendingEntry:
    proposal: ReminderProposal
    expires_at: float


class PendingProposalStore:
    """Bounded in-process store of unconfirmed proposals."""

    def __init__(
        self, *, ttl_seconds: float = PROPOSAL_TTL_SECONDS, max_entries: int = MAX_OPEN_PROPOSALS
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._entries: dict[str, _PendingEntry] = {}

    def put(self, proposal: ReminderProposal) -> None:
        if len(self._entries) >= self._max:
            oldest_key = min(self._entries, key=lambda key: self._entries[key].expires_at)
            self._entries.pop(oldest_key)
        self._entries[proposal.token] = _PendingEntry(proposal, monotonic() + self._ttl)

    def get(self, token: str, owner_user_id: int) -> ReminderProposal | None:
        entry = self._entries.get(token)
        if entry is None or monotonic() > entry.expires_at:
            self._entries.pop(token, None)
            return None
        if entry.proposal.owner_user_id != owner_user_id:
            return None
        return entry.proposal

    def pop(self, token: str, owner_user_id: int) -> ReminderProposal | None:
        proposal = self.get(token, owner_user_id)
        if proposal is not None:
            self._entries.pop(token, None)
        return proposal

    def remove(self, token: str) -> None:
        self._entries.pop(token, None)


class ReminderService:
    """Creates proposals from messages and writes confirmed reminders."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Clock,
        pending: PendingProposalStore | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self.pending = pending or PendingProposalStore()

    def build_proposal(self, user: User, message_text: str) -> ReminderProposal | None:
        """Parse a message; return None when it carries no time expression."""
        result = parse_reminder_time(message_text, clock=self._clock, timezone_name=user.timezone)
        due = result.due_at_utc or result.suggested_due_at_utc
        if due is None:
            return None
        text = result.reminder_text or message_text.strip()
        return ReminderProposal(
            token=uuid4().hex[:12],
            owner_user_id=user.id,
            reminder_text=text[:500],
            due_at_utc=due,
            timezone_name=user.timezone,
            needs_clarification=result.needs_clarification,
            clarification_reason=result.clarification_reason,
        )

    def propose(self, user: User, message_text: str) -> ReminderProposal | None:
        proposal = self.build_proposal(user, message_text)
        if proposal is not None:
            self.pending.put(proposal)
        return proposal

    def repropose(
        self, user: User, previous_token: str, new_message_text: str
    ) -> ReminderProposal | None:
        """Re-parse after an Edit request; replaces the previous proposal."""
        previous = self.pending.get(previous_token, user.id)
        if previous is None:
            return None
        result = parse_reminder_time(
            new_message_text, clock=self._clock, timezone_name=user.timezone
        )
        due = result.due_at_utc or result.suggested_due_at_utc
        if due is None:
            return None
        self.pending.remove(previous_token)
        proposal = ReminderProposal(
            token=uuid4().hex[:12],
            owner_user_id=user.id,
            reminder_text=previous.reminder_text,
            due_at_utc=due,
            timezone_name=user.timezone,
            needs_clarification=result.needs_clarification,
            clarification_reason=result.clarification_reason,
        )
        self.pending.put(proposal)
        return proposal

    async def confirm(self, user: User, token: str) -> int | None:
        """Persist a confirmed reminder. Returns the reminder id or None."""
        proposal = self.pending.pop(token, user.id)
        if proposal is None:
            return None
        reminder = Reminder(
            user_id=user.id,
            text=proposal.reminder_text,
            timezone=proposal.timezone_name,
            due_at_utc=proposal.due_at_utc,
            next_run_at=proposal.due_at_utc,
            status=ReminderStatus.ACTIVE.value,
            idempotency_key=f"prop:{proposal.token}",
        )
        async with self._session_factory.begin() as session:
            session.add(reminder)
            await session.flush()
            return int(reminder.id)

    def discard(self, user: User, token: str) -> bool:
        proposal = self.pending.pop(token, user.id)
        return proposal is not None

    async def list_active(self, user: User) -> list[Reminder]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Reminder)
                    .where(
                        Reminder.user_id == user.id,
                        Reminder.status.in_(
                            [ReminderStatus.ACTIVE.value, ReminderStatus.PAUSED.value]
                        ),
                    )
                    .order_by(Reminder.due_at_utc)
                    .limit(50)
                )
            ).scalars()
            return list(rows.all())

    async def cancel(self, user: User, reminder_id: int) -> bool:
        async with self._session_factory.begin() as session:
            reminder = await session.get(Reminder, reminder_id)
            if reminder is None or reminder.user_id != user.id:
                return False
            if reminder.status not in (ReminderStatus.ACTIVE.value, ReminderStatus.PAUSED.value):
                return False
            reminder.status = ReminderStatus.CANCELLED.value
            return True

    async def delete_personal_data(self, user: User) -> dict[str, int]:
        """Delete the caller's personal content (SPEC §4.1 /verilerimi_sil).

        Removes conversations, reminders, and open ingestion sessions. The
        user row (identity/allowlist state) and audit history are preserved.
        """
        async with self._session_factory.begin() as session:
            conversations = (
                await session.execute(
                    select(func.count())
                    .select_from(AIConversation)
                    .where(AIConversation.user_id == user.id)
                )
            ).scalar_one()
            reminders = (
                await session.execute(
                    select(func.count()).select_from(Reminder).where(Reminder.user_id == user.id)
                )
            ).scalar_one()
            sessions = (
                await session.execute(
                    select(func.count())
                    .select_from(IngestionSession)
                    .where(IngestionSession.user_id == user.id)
                )
            ).scalar_one()
            await session.execute(delete(AIConversation).where(AIConversation.user_id == user.id))
            await session.execute(delete(Reminder).where(Reminder.user_id == user.id))
            await session.execute(
                delete(IngestionSession).where(IngestionSession.user_id == user.id)
            )
        return {
            "conversations": int(conversations),
            "reminders": int(reminders),
            "sessions": int(sessions),
        }
