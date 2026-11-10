"""Order workflow rules: state transitions, intake order and derived overdue.

Owner contract V2 §3.2 and §3.4. These are the decisions the Telegram adapter must never
re-implement, so they are tested here without a database, a clock or a network.
"""

from datetime import UTC, datetime, timedelta

import pytest

from office_assistant.infrastructure.database.models import OrderState
from office_assistant.orders import workflow

DEADLINE = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)

# The five answers the contract requires before a draft may become a live order.
REQUIRED: dict[str, object] = {
    "deadline": DEADLINE,
    "lead_time": 2880,
    "warnings": 5,
    "requester": "Aysel",
    "description": "10 metr kabel",
}


def _as_draft(answers: dict[str, object]) -> dict[str, object]:
    """Answers shaped like the service builds them: every intake field, none invented."""

    return {field: answers.get(field) for field in workflow.INTAKE_ORDER}


def _draft(**overrides: object) -> dict[str, object]:
    """A draft with the required answers filled, or any of them replaced."""

    return _as_draft({**REQUIRED, **overrides})


def test_intake_asks_the_six_questions_in_the_contract_order() -> None:
    assert workflow.INTAKE_ORDER == (
        "deadline",
        "lead_time",
        "warnings",
        "requester",
        "description",
        "notes",
    )


def test_next_open_field_walks_the_list_in_order_and_stops_at_the_first_gap() -> None:
    assert workflow.next_open_field(_as_draft({})) == "deadline"
    assert workflow.next_open_field(_as_draft({"deadline": DEADLINE})) == "lead_time"
    assert workflow.next_open_field(_as_draft({"notes": "qeyd"})) == "deadline"
    # notes is asked last; until it is answered or skipped it is still an open question.
    assert workflow.next_open_field(_draft()) == "notes"
    assert workflow.next_open_field(_draft(notes="qeyd")) is None


def test_notes_may_stay_empty_because_only_it_is_optional() -> None:
    assert workflow.is_complete(_draft())
    assert workflow.is_complete(_draft(notes="qeyd"))
    assert not workflow.is_complete(_draft(requester=""))
    assert not workflow.is_complete(_draft(warnings=0))
    assert not workflow.is_complete(_as_draft({}))


@pytest.mark.parametrize(
    ("state", "action", "expected"),
    [
        (OrderState.PENDING_CONFIRMATION, workflow.OrderAction.CONFIRM, OrderState.ACTIVE),
        (OrderState.PENDING_CONFIRMATION, workflow.OrderAction.CANCEL, OrderState.CANCELLED),
        (OrderState.ACTIVE, workflow.OrderAction.FREEZE, OrderState.FROZEN),
        (OrderState.ACTIVE, workflow.OrderAction.COMPLETE, OrderState.COMPLETED),
        (OrderState.ACTIVE, workflow.OrderAction.CANCEL, OrderState.CANCELLED),
        (OrderState.FROZEN, workflow.OrderAction.RESUME, OrderState.ACTIVE),
        (OrderState.FROZEN, workflow.OrderAction.COMPLETE, None),
        (OrderState.COMPLETED, workflow.OrderAction.RESUME, None),
        (OrderState.COMPLETED, workflow.OrderAction.FREEZE, None),
        (OrderState.COMPLETED, workflow.OrderAction.COMPLETE, None),
        (OrderState.CANCELLED, workflow.OrderAction.RESUME, None),
        (OrderState.DELETED, workflow.OrderAction.RESUME, None),
    ],
)
def test_only_the_documented_moves_are_allowed(
    state: OrderState, action: workflow.OrderAction, expected: OrderState | None
) -> None:
    if expected is None:
        with pytest.raises(workflow.InvalidTransition):
            workflow.next_state(state, action)
        return
    assert workflow.next_state(state, action) is expected


def test_every_live_state_can_reach_the_recoverable_deleted_state() -> None:
    """Sil is a soft delete, so it must be reachable from any state but itself."""

    for state in (
        OrderState.PENDING_CONFIRMATION,
        OrderState.ACTIVE,
        OrderState.FROZEN,
        OrderState.COMPLETED,
        OrderState.CANCELLED,
    ):
        assert workflow.next_state(state, workflow.OrderAction.DELETE) is OrderState.DELETED
    with pytest.raises(workflow.InvalidTransition):
        workflow.next_state(OrderState.DELETED, workflow.OrderAction.DELETE)


def test_each_action_names_the_timestamp_it_records() -> None:
    assert workflow.stamp_field(workflow.OrderAction.CONFIRM) == "confirmed_at"
    assert workflow.stamp_field(workflow.OrderAction.COMPLETE) == "completed_at"
    assert workflow.stamp_field(workflow.OrderAction.FREEZE) == "frozen_at"
    assert workflow.stamp_field(workflow.OrderAction.DELETE) == "deleted_at"
    assert workflow.stamp_field(workflow.OrderAction.RESUME) is None


def test_overdue_is_derived_and_never_claims_a_state_it_was_not_given() -> None:
    late = DEADLINE + timedelta(minutes=1)

    assert workflow.is_overdue(OrderState.ACTIVE, DEADLINE, late)
    assert not workflow.is_overdue(OrderState.FROZEN, DEADLINE, late)
    assert not workflow.is_overdue(OrderState.COMPLETED, DEADLINE, late)
    assert not workflow.is_overdue(OrderState.ACTIVE, DEADLINE, DEADLINE - timedelta(minutes=1))
    assert not workflow.is_overdue(OrderState.ACTIVE, None, late)
