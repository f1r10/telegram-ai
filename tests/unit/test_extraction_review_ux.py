"""Issue 2 regression tests: extraction review callbacks report the real failure."""

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from telegram import Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.errors import (
    ApplicationError,
    AuthorizationDenied,
    DomainConflict,
    InvalidStateTransition,
)
from office_assistant.application.user_management import UserManagementService
from office_assistant.extraction.review import ApplyResult, ExtractionReviewService
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.handlers import (
    SERVICES_KEY,
    TelegramServices,
    handle_extraction_callback,
)
from office_assistant.integrations.telegram.state import (
    PasswordAttemptLimiter,
    PasswordEntryState,
)


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
    callback_query: FakeQuery | None = None
    update_id: int = 1
    effective_id: int = 5


def _reviews(approve: AsyncMock) -> ExtractionReviewService:
    return cast(
        ExtractionReviewService,
        SimpleNamespace(
            approve=approve,
            problems=AsyncMock(return_value=[]),
            reject=AsyncMock(),
        ),
    )


def _run(review_service: ExtractionReviewService, data: str) -> FakeQuery:
    services = TelegramServices(
        authentication=cast(AuthenticationService, SimpleNamespace()),
        users=cast(UserManagementService, SimpleNamespace()),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
        extraction_reviews=review_service,
    )
    query = FakeQuery(data=data)
    update = FakeUpdate(
        effective_user=TelegramUser(123, "Test", False, username="test"),
        effective_chat=FakeChat(),
        callback_query=query,
    )
    context = SimpleNamespace(bot_data={SERVICES_KEY: services})
    asyncio.run(
        handle_extraction_callback(cast(Update, update), cast(ContextTypes.DEFAULT_TYPE, context))
    )
    return query


def _approve_callback_data(run_id: UUID | None = None) -> str:
    return f"xr:a:{run_id or uuid4()}"


def test_approved_review_reports_the_applied_counts() -> None:
    approve = AsyncMock(
        return_value=ApplyResult(applied=6, already_exists=1, needs_admin=0, failed=0)
    )
    query = _run(_reviews(approve), _approve_callback_data())
    assert approve.await_args is not None
    assert query.edited == [
        messages.EXTRACTION_APPLIED.format(applied=6, existing=1, admin=0, failed=0)
    ]


def test_only_authorization_denial_is_reported_as_a_permission_problem() -> None:
    query = _run(
        _reviews(AsyncMock(side_effect=AuthorizationDenied("not an admin"))), "xr:a:" + str(uuid4())
    )
    assert query.edited == [messages.NO_PERMISSION]


def test_missing_or_finished_run_is_not_reported_as_a_permission_problem() -> None:
    query = _run(
        _reviews(AsyncMock(side_effect=DomainConflict("Extraction run does not exist"))),
        _approve_callback_data(),
    )
    assert query.edited == [messages.EXTRACTION_REVIEW_UNAVAILABLE]
    assert messages.NO_PERMISSION not in query.edited


def test_other_application_errors_degrade_to_the_temporary_failure_message() -> None:
    for error in (
        InvalidStateTransition("Review already closed"),
        ApplicationError("unexpected"),
        ValueError("Unsupported review action"),
    ):
        query = _run(_reviews(AsyncMock(side_effect=error)), _approve_callback_data())
        assert query.edited == [messages.TEMPORARY_ERROR]
        assert messages.NO_PERMISSION not in query.edited


def test_review_callbacks_for_unknown_actions_are_not_permission_denials() -> None:
    query = _run(_reviews(AsyncMock()), "xr:z:" + str(uuid4()))
    assert query.edited == [messages.TEMPORARY_ERROR]
