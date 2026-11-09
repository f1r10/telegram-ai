"""Order workflow rules: which action a state accepts, and what overdue means.

The stored states are the six in the owner contract; ``OVERDUE`` is derived from the
deadline so that a clock change can never make a record claim a state it was not given.
Freezing stops future review reminders and resuming recomputes only the ones still ahead
of now, so resuming never produces a burst.
"""

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum

from office_assistant.infrastructure.database.models import OrderState

INTAKE_ORDER = ("deadline", "lead_time", "warnings", "requester", "description", "notes")
OPTIONAL_INTAKE_FIELDS = frozenset({"notes"})


class OrderAction(StrEnum):
    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"
    COMPLETE = "COMPLETE"
    FREEZE = "FREEZE"
    RESUME = "RESUME"
    DELETE = "DELETE"


class InvalidTransition(Exception):
    """The order is not in a state that accepts this action."""


_TRANSITIONS: dict[tuple[OrderState, OrderAction], OrderState] = {
    (OrderState.PENDING_CONFIRMATION, OrderAction.CONFIRM): OrderState.ACTIVE,
    (OrderState.PENDING_CONFIRMATION, OrderAction.CANCEL): OrderState.CANCELLED,
    (OrderState.PENDING_CONFIRMATION, OrderAction.DELETE): OrderState.DELETED,
    (OrderState.ACTIVE, OrderAction.COMPLETE): OrderState.COMPLETED,
    (OrderState.ACTIVE, OrderAction.FREEZE): OrderState.FROZEN,
    (OrderState.ACTIVE, OrderAction.CANCEL): OrderState.CANCELLED,
    (OrderState.ACTIVE, OrderAction.DELETE): OrderState.DELETED,
    (OrderState.FROZEN, OrderAction.RESUME): OrderState.ACTIVE,
    (OrderState.FROZEN, OrderAction.CANCEL): OrderState.CANCELLED,
    (OrderState.FROZEN, OrderAction.DELETE): OrderState.DELETED,
    (OrderState.COMPLETED, OrderAction.DELETE): OrderState.DELETED,
    (OrderState.CANCELLED, OrderAction.DELETE): OrderState.DELETED,
}

_STAMPED_AT: dict[OrderAction, str] = {
    OrderAction.CONFIRM: "confirmed_at",
    OrderAction.COMPLETE: "completed_at",
    OrderAction.FREEZE: "frozen_at",
    OrderAction.CANCEL: "cancelled_at",
    OrderAction.DELETE: "deleted_at",
}


def next_state(state: OrderState | str, action: OrderAction) -> OrderState:
    """The state an action moves a live order to, or ``InvalidTransition``."""

    key = (OrderState(state), action)
    target = _TRANSITIONS.get(key)
    if target is None:
        raise InvalidTransition(f"{action.value} is not allowed from {key[0].value}")
    return target


def stamp_field(action: OrderAction) -> str | None:
    """The timestamp column an action records, so the history stays explainable."""

    return _STAMPED_AT.get(action)


def is_terminal(state: OrderState | str) -> bool:
    return OrderState(state) in {OrderState.COMPLETED, OrderState.CANCELLED, OrderState.DELETED}


def is_overdue(state: OrderState | str, deadline_utc: datetime | None, now: datetime) -> bool:
    """Derived only for a running order: a frozen or finished one is not late."""

    if OrderState(state) is not OrderState.ACTIVE or deadline_utc is None:
        return False
    return now > deadline_utc


def is_complete(values: Mapping[str, object]) -> bool:
    """Everything the contract requires before a draft may become a live order.

    Only the five required answers count; ``notes`` is the last question but a skippable
    one, so a draft without a note is still confirmable.
    """

    return all(
        _filled(values.get(field))
        for field in INTAKE_ORDER
        if field not in OPTIONAL_INTAKE_FIELDS
    )


def next_open_field(values: Mapping[str, object]) -> str | None:
    """The question to ask next, or ``None`` when the draft can be reviewed."""

    return next((field for field in INTAKE_ORDER if not _filled(values.get(field))), None)


def _filled(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, int):
        return value > 0
    return True
