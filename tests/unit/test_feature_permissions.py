"""Per-user capability toggles: one policy, enforced in the menu, the callback and the service.

Owner contract V2 §2 and §6: an administrator approves a binary grid per user, the backend
re-authorizes every turn (including a stale button press), and an administrator can never be
locked out by a toggle.
"""

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import CheckConstraint, Table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import Update
from telegram import User as TelegramUser
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from office_assistant.application.authentication import AuthenticationService
from office_assistant.application.business_common import FeatureAccessDenied, ensure_feature
from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.feature_permissions import (
    FeaturePermissionService,
    UnknownFeatureTarget,
    _grid,  # pyright: ignore[reportPrivateUsage]
)
from office_assistant.application.user_management import UserManagementService
from office_assistant.auth.features import (
    BASELINE_ENABLED,
    FEATURE_KEYS,
    FeatureKey,
    allowed_features,
    baseline_grants,
    can_use_feature,
    parse_feature,
)
from office_assistant.infrastructure.database.models import (
    User,
    UserFeaturePermission,
    UserRole,
    UserStatus,
)
from office_assistant.integrations.telegram import messages
from office_assistant.integrations.telegram.handlers import (
    MENU_SELECTION_KEY,
    TelegramServices,
    handle_feature_callback,
    handle_menu,
    handle_menu_callback,
)
from office_assistant.integrations.telegram.state import (
    PasswordAttemptLimiter,
    PasswordEntryState,
)


def _user(
    status: UserStatus = UserStatus.ACTIVE,
    telegram_user_id: int = 123,
    role: UserRole = UserRole.USER,
) -> User:
    return User(
        id=telegram_user_id - 122,
        telegram_user_id=telegram_user_id,
        role=role,
        status=status,
    )


def _context(user: User | None) -> tuple[SimpleNamespace, TelegramServices]:
    lookup = AsyncMock(return_value=user)
    services = TelegramServices(
        authentication=cast(AuthenticationService, SimpleNamespace()),
        users=cast(UserManagementService, SimpleNamespace(get_by_telegram_id=lookup)),
        password_state=PasswordEntryState(),
        limiter=PasswordAttemptLimiter(),
    )
    context = SimpleNamespace(
        bot_data={"phase2_services": services},
        user_data={},
        bot=SimpleNamespace(send_message=AsyncMock(), send_chat_action=AsyncMock()),
    )
    return context, services


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


def _update(message: SimpleNamespace, query: SimpleNamespace | None = None) -> Update:
    fake = SimpleNamespace(
        effective_user=TelegramUser(123, "Test", False),
        effective_chat=SimpleNamespace(type=ChatType.PRIVATE, id=123),
        effective_message=message,
        callback_query=query,
        update_id=17,
    )
    return cast(Update, fake)


def _query(data: str) -> SimpleNamespace:
    return SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())


def _run_menu_callback(context: SimpleNamespace, data: str) -> SimpleNamespace:
    query = _query(data)
    asyncio.run(
        handle_menu_callback(
            _update(_message("/menu"), query), cast(ContextTypes.DEFAULT_TYPE, context)
        )
    )
    return query


def _grants(**overrides: bool) -> dict[str, bool]:
    values = baseline_grants()
    values.update(overrides)
    return values


def test_feature_labels_cover_exactly_the_declared_keys() -> None:
    assert set(messages.FEATURE_LABELS) == {key.value for key in FEATURE_KEYS}


def test_parse_feature_accepts_only_declared_keys() -> None:
    assert parse_feature("image_search") is FeatureKey.IMAGE_SEARCH
    assert parse_feature(FeatureKey.ORDERS) is FeatureKey.ORDERS
    assert parse_feature("orders_all") is None
    assert parse_feature(None) is None


def test_admin_is_never_locked_out_even_when_every_grant_is_off() -> None:
    admin = _user(role=UserRole.ADMIN)
    off = {key.value: False for key in FEATURE_KEYS}
    assert all(can_use_feature(admin, off, key) for key in FEATURE_KEYS)
    assert allowed_features(admin, off) == FEATURE_KEYS


def test_a_missing_grant_row_uses_the_documented_baseline_not_a_silent_denial() -> None:
    user = _user()
    assert can_use_feature(user, {}, FeatureKey.DOCUMENT_SEARCH)
    assert not can_use_feature(user, {}, FeatureKey.ORDERS)
    assert FeatureKey.ORDERS not in BASELINE_ENABLED
    assert {key for key in FEATURE_KEYS if key in BASELINE_ENABLED} == {
        key for key in FEATURE_KEYS if baseline_grants()[key.value]
    }


@pytest.mark.parametrize("key", list(FEATURE_KEYS))
def test_only_an_active_account_exercises_any_capability(key: FeatureKey) -> None:
    granted = _grants(**{key.value: True})
    assert not can_use_feature(_user(status=UserStatus.PENDING), granted, key)
    assert not can_use_feature(_user(status=UserStatus.DISABLED), granted, key)
    assert not can_use_feature(None, granted, key)
    assert can_use_feature(_user(), granted, key)


def _grant_session(rows: list[tuple[str, bool]]) -> SimpleNamespace:
    """A session double for `.execute(...).tuples().all()`.

    The result object is built separately: children created from an ``AsyncMock``
    return value are asynchronous themselves, so `.all()` would hand back a coroutine.
    """

    result = Mock()
    result.tuples.return_value.all.return_value = rows
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    return session


def test_the_service_boundary_denies_a_disabled_capability() -> None:
    session = _grant_session([("image_search", False)])
    with pytest.raises(FeatureAccessDenied) as denied:
        asyncio.run(ensure_feature(cast(AsyncSession, session), _user(), FeatureKey.IMAGE_SEARCH))
    assert denied.value.feature_key == "image_search"


def test_the_service_boundary_allows_an_explicit_grant() -> None:
    session = _grant_session([("image_search", True)])
    asyncio.run(ensure_feature(cast(AsyncSession, session), _user(), FeatureKey.IMAGE_SEARCH))


def test_the_service_boundary_never_reads_grants_for_an_administrator() -> None:
    session = SimpleNamespace(execute=AsyncMock(side_effect=AssertionError("grant read")))
    admin = _user(role=UserRole.ADMIN)
    asyncio.run(ensure_feature(cast(AsyncSession, session), admin, FeatureKey.ORDERS))


def test_a_stale_menu_button_is_reauthorized_on_the_current_grants() -> None:
    context, services = _context(_user())
    grants = _grants(image_search=False)
    services.features = cast(
        FeaturePermissionService,
        SimpleNamespace(grants_for=AsyncMock(return_value=grants)),
    )
    query = _run_menu_callback(context, "om:photo")
    query.edit_message_text.assert_awaited_once_with(messages.FEATURE_DISABLED)
    assert MENU_SELECTION_KEY not in context.user_data


def test_an_ordinary_user_cannot_open_the_administrator_users_panel() -> None:
    context, services = _context(_user())
    services.features = cast(
        FeaturePermissionService,
        SimpleNamespace(grid_for_actor=AsyncMock(side_effect=AssertionError("admin path reached"))),
    )
    query = _run_menu_callback(context, "om:users")
    query.edit_message_text.assert_awaited_once_with(messages.NO_PERMISSION)


def test_an_administrator_toggles_a_feature_through_the_callback() -> None:
    target = _user(telegram_user_id=456)
    context, services = _context(_user(role=UserRole.ADMIN))
    grid = _grid(target, _grants(document_ingest=False))
    service = SimpleNamespace(
        set_for_actor=AsyncMock(return_value=grid),
        grants_for=AsyncMock(return_value=_grants()),
    )
    services.features = cast(FeaturePermissionService, service)
    query = _query("uf:334:document_ingest:off")
    asyncio.run(
        handle_feature_callback(
            _update(_message("x"), query), cast(ContextTypes.DEFAULT_TYPE, context)
        )
    )
    service.set_for_actor.assert_awaited_once_with(123, 334, FeatureKey.DOCUMENT_INGEST, False)
    text = query.edit_message_text.await_args.args[0]
    assert text.startswith(messages.FEATURE_UPDATED.split("{label}")[0])
    assert messages.FEATURE_LABELS["document_ingest"] in text
    assert messages.FEATURE_STATE_OFF in text


@pytest.mark.parametrize(
    "data",
    ["uf:334", "uf:x:334:orders:on", "uf:334:not_a_key:on", "uf:334:orders:maybe"],
)
def test_a_malformed_toggle_callback_writes_nothing(data: str) -> None:
    context, services = _context(_user(role=UserRole.ADMIN))
    service = SimpleNamespace(set_for_actor=AsyncMock(side_effect=AssertionError("write reached")))
    services.features = cast(FeaturePermissionService, service)
    query = _query(data)
    asyncio.run(
        handle_feature_callback(
            _update(_message("x"), query), cast(ContextTypes.DEFAULT_TYPE, context)
        )
    )
    service.set_for_actor.assert_not_awaited()


def test_a_stale_toggle_press_is_refused_by_the_service_boundary() -> None:
    """The pressed button is client input; only the service decides and the handler renders it."""

    context, services = _context(_user(status=UserStatus.PENDING))
    service = SimpleNamespace(
        set_for_actor=AsyncMock(side_effect=AuthorizationDenied("Only an active administrator")),
    )
    services.features = cast(FeaturePermissionService, service)
    query = _query("uf:334:orders:on")
    asyncio.run(
        handle_feature_callback(
            _update(_message("x"), query), cast(ContextTypes.DEFAULT_TYPE, context)
        )
    )
    service.set_for_actor.assert_awaited_once_with(123, 334, FeatureKey.ORDERS, True)
    query.edit_message_text.assert_awaited_once_with(messages.NO_PERMISSION)


def test_the_admin_grid_is_read_only_and_a_user_grid_covers_every_key() -> None:
    admin_view = _grid(_user(role=UserRole.ADMIN), _grants())
    user_view = _grid(_user(), _grants())
    assert not admin_view.editable
    assert user_view.editable
    assert [view.feature_key for view in user_view.grants] == [key.value for key in FEATURE_KEYS]
    assert all(view.effective for view in admin_view.grants)
    assert all(
        view.effective == can_use_feature(user_view.user, _grants(), key)
        for view, key in zip(user_view.grants, FEATURE_KEYS, strict=True)
    )


def test_an_unknown_feature_key_is_refused_before_any_session_opens() -> None:
    factory = cast(async_sessionmaker[AsyncSession], SimpleNamespace())
    service = FeaturePermissionService(factory)
    with pytest.raises(UnknownFeatureTarget):
        asyncio.run(service.set_for_actor(123, 334, "nonsense", True))


def test_the_database_key_constraint_lists_every_declared_feature() -> None:
    """The models module cannot import the policy, so this test is the only link."""

    declared = str(
        next(
            check.sqltext
            for check in cast(Table, UserFeaturePermission.__table__).constraints
            if isinstance(check, CheckConstraint)
            and check.name == "ck_user_feature_permissions_key"
        )
    )
    for key in FEATURE_KEYS:
        assert f"'{key.value}'" in declared
    assert declared.count("'") == 2 * len(FEATURE_KEYS)


def test_the_menu_offers_the_users_entry_only_to_an_administrator() -> None:
    context, _ = _context(_user(role=UserRole.ADMIN))
    message = _message("/menu")
    asyncio.run(handle_menu(_update(message), cast(ContextTypes.DEFAULT_TYPE, context)))
    rows = [
        [button.callback_data for button in row]
        for row in message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
    ]
    assert "om:users" in rows[-1]
