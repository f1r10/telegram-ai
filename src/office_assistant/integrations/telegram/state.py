"""Bounded in-process state for password entry, auth failures and reminder flows."""

from dataclasses import dataclass
from time import monotonic


@dataclass(slots=True)
class FailureState:
    count: int
    first_failure_at: float
    blocked_until: float = 0.0


class PasswordAttemptLimiter:
    """Best-effort V1 limiter; authorization itself remains database-backed."""

    def __init__(self, *, max_attempts: int = 5, window_seconds: float = 300.0) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._states: dict[int, FailureState] = {}

    def is_blocked(self, telegram_user_id: int) -> bool:
        state = self._states.get(telegram_user_id)
        if state is None:
            return False
        now = monotonic()
        if state.blocked_until > now:
            return True
        if now - state.first_failure_at >= self.window_seconds:
            self._states.pop(telegram_user_id, None)
        return False

    def register_failure(self, telegram_user_id: int) -> None:
        now = monotonic()
        state = self._states.get(telegram_user_id)
        if state is None or now - state.first_failure_at >= self.window_seconds:
            state = FailureState(count=0, first_failure_at=now)
            self._states[telegram_user_id] = state
        state.count += 1
        if state.count >= self.max_attempts:
            state.blocked_until = now + self.window_seconds
        if len(self._states) > 1000:
            oldest = min(self._states, key=lambda key: self._states[key].first_failure_at)
            self._states.pop(oldest, None)

    def clear(self, telegram_user_id: int) -> None:
        self._states.pop(telegram_user_id, None)


class PasswordEntryState:
    """Bounded set of users currently expected to send a password."""

    def __init__(self, max_users: int = 1000) -> None:
        self.max_users = max_users
        self._waiting: set[int] = set()

    def request(self, telegram_user_id: int) -> None:
        if len(self._waiting) >= self.max_users:
            self._waiting.pop()
        self._waiting.add(telegram_user_id)

    def contains(self, telegram_user_id: int) -> bool:
        return telegram_user_id in self._waiting

    def clear(self, telegram_user_id: int) -> None:
        self._waiting.discard(telegram_user_id)


class ReminderEditState:
    """Bounded map of users currently expected to type a new reminder time."""

    def __init__(self, max_users: int = 1000) -> None:
        self.max_users = max_users
        self._tokens: dict[int, str] = {}

    def request(self, telegram_user_id: int, proposal_token: str) -> None:
        if len(self._tokens) >= self.max_users:
            self._tokens.pop(next(iter(self._tokens)))
        self._tokens[telegram_user_id] = proposal_token

    def token_for(self, telegram_user_id: int) -> str | None:
        return self._tokens.get(telegram_user_id)

    def clear(self, telegram_user_id: int) -> None:
        self._tokens.pop(telegram_user_id, None)


class DeleteConfirmationState:
    """Bounded set of users with an open personal-data deletion confirmation."""

    def __init__(self, max_users: int = 1000) -> None:
        self.max_users = max_users
        self._waiting: set[int] = set()

    def request(self, telegram_user_id: int) -> None:
        if len(self._waiting) >= self.max_users:
            self._waiting.pop()
        self._waiting.add(telegram_user_id)

    def contains(self, telegram_user_id: int) -> bool:
        return telegram_user_id in self._waiting

    def clear(self, telegram_user_id: int) -> None:
        self._waiting.discard(telegram_user_id)


class ReferenceRevokeState:
    """Bounded map of the product code each administrator was last asked to revoke a picture for.

    The code is kept here rather than in the button's callback data: a callback is client-supplied,
    and an unverified string that names whose confirmation to retire is one privilege too far.
    """

    def __init__(self, max_users: int = 1000) -> None:
        self.max_users = max_users
        self._codes: dict[int, str] = {}

    def request(self, telegram_user_id: int, canonical_code: str) -> None:
        if len(self._codes) >= self.max_users:
            self._codes.pop(next(iter(self._codes)))
        self._codes[telegram_user_id] = canonical_code

    def code_for(self, telegram_user_id: int) -> str | None:
        return self._codes.get(telegram_user_id)

    def clear(self, telegram_user_id: int) -> None:
        self._codes.pop(telegram_user_id, None)
