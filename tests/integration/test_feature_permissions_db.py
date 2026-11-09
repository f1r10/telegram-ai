"""Real PostgreSQL evidence for the per-user capability grid (owner contract V2 §2, §6).

SQLite and mocks are not equivalent here: the cascade, the key CHECK constraint, the
row lock in the toggle and the approval transaction are all PostgreSQL behaviour.
"""

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_common import FeatureAccessDenied, require_feature
from office_assistant.application.errors import AuthorizationDenied
from office_assistant.application.feature_permissions import (
    AdministratorFeatureLocked,
    FeaturePermissionService,
    UnknownFeatureTarget,
)
from office_assistant.application.user_management import UserManagementService
from office_assistant.auth.features import FEATURE_KEYS, FeatureKey, baseline_grants
from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    User,
    UserFeaturePermission,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory

pytestmark = pytest.mark.integration
ADMIN_TG = 99998121
STAFF_TG = 99998122
OTHER_TG = 99998123
PENDING_TG = 99998124
TEST_TG = (ADMIN_TG, STAFF_TG, OTHER_TG, PENDING_TG)

Factory = async_sessionmaker[AsyncSession]
Scenario = Callable[[Factory], Awaitable[None]]


def _scenario(function: Scenario) -> None:
    settings = load_settings()
    if settings.postgres_db != "office_assistant_test":
        pytest.skip("Capability grids are only written on disposable CI PostgreSQL")
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)

    async def cleanup() -> None:
        async with factory.begin() as session:
            ids = select(User.id).where(User.telegram_user_id.in_(TEST_TG))
            await session.execute(
                delete(AuditEvent).where(AuditEvent.entity_type == "user_feature")
            )
            await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(ids)))
            await session.execute(
                delete(UserFeaturePermission).where(UserFeaturePermission.user_id.in_(ids))
            )
            await session.execute(delete(User).where(User.telegram_user_id.in_(TEST_TG)))

    async def seeded() -> None:
        async with factory.begin() as session:
            session.add_all(
                [
                    User(
                        telegram_user_id=ADMIN_TG,
                        role=UserRole.ADMIN,
                        status=UserStatus.ACTIVE,
                    ),
                    User(
                        telegram_user_id=STAFF_TG,
                        role=UserRole.USER,
                        status=UserStatus.ACTIVE,
                    ),
                    User(
                        telegram_user_id=OTHER_TG,
                        role=UserRole.USER,
                        status=UserStatus.ACTIVE,
                    ),
                    User(
                        telegram_user_id=PENDING_TG,
                        role=UserRole.USER,
                        status=UserStatus.PENDING,
                    ),
                ]
            )

    async def runner() -> None:
        await cleanup()
        try:
            await seeded()
            await function(factory)
        finally:
            await cleanup()
        await engine.dispose()

    asyncio.run(runner())


async def _id(factory: Factory, telegram_user_id: int) -> int:
    async with factory() as session:
        found = await session.scalar(
            select(User.id).where(User.telegram_user_id == telegram_user_id)
        )
    assert found is not None
    return int(found)


def test_every_active_user_row_is_covered_by_an_explicit_baseline_grid() -> None:
    async def scenario(factory: Factory) -> None:
        service = FeaturePermissionService(factory)
        grants = await service.grants_for(await _user(factory, STAFF_TG))
        assert grants == baseline_grants()
        assert not grants[FeatureKey.ORDERS.value]
        assert await _count(factory, await _id(factory, STAFF_TG)) == len(FEATURE_KEYS)

    _scenario(scenario)


async def _user(factory: Factory, telegram_user_id: int) -> User:
    async with factory() as session:
        found = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
        assert found is not None
        return found


def test_approval_writes_the_grid_in_the_same_transaction() -> None:
    async def scenario(factory: Factory) -> None:
        pending = await _id(factory, PENDING_TG)
        before = await _count(factory, pending)
        await UserManagementService(factory).approve(ADMIN_TG, pending)
        assert await _count(factory, pending) == before + len(FEATURE_KEYS)
        grants = await FeaturePermissionService(factory).grants_for(
            await _user(factory, PENDING_TG)
        )
        assert grants == baseline_grants()

    _scenario(scenario)


async def _count(factory: Factory, user_id: int) -> int:
    async with factory() as session:
        total = await session.scalar(
            select(func.count())
            .select_from(UserFeaturePermission)
            .where(UserFeaturePermission.user_id == user_id)
        )
    assert total is not None
    return int(total)


def test_a_disabled_capability_is_denied_at_the_service_boundary() -> None:
    async def scenario(factory: Factory) -> None:
        service = FeaturePermissionService(factory)
        await service.set_for_actor(
            ADMIN_TG, await _id(factory, STAFF_TG), "document_search", False
        )
        with pytest.raises(FeatureAccessDenied) as denied:
            async with factory() as session:
                await require_feature(session, STAFF_TG, FeatureKey.DOCUMENT_SEARCH)
        assert denied.value.feature_key == "document_search"
        # An unrelated capability keeps working: this is a scope change, not a lockout.
        async with factory() as session:
            actor = await require_feature(session, STAFF_TG, FeatureKey.AI_CHAT)
        assert actor.telegram_user_id == STAFF_TG

    _scenario(scenario)


def test_an_administrator_toggle_never_locks_the_administrator_out() -> None:
    async def scenario(factory: Factory) -> None:
        service = FeaturePermissionService(factory)
        admin = await _id(factory, ADMIN_TG)
        with pytest.raises(AdministratorFeatureLocked):
            await service.set_for_actor(ADMIN_TG, admin, "document_search", False)
        async with factory() as session:
            for key in FEATURE_KEYS:
                assert (await require_feature(session, ADMIN_TG, key)).role == UserRole.ADMIN

    _scenario(scenario)


def test_a_toggle_takes_effect_on_the_next_read_without_a_restart() -> None:
    async def scenario(factory: Factory) -> None:
        service = FeaturePermissionService(factory)
        staff = await _user(factory, STAFF_TG)
        assert (await service.allowed_for(staff)).count(FeatureKey.SALES_REPORTS) == 1
        grid = await service.set_for_actor(
            ADMIN_TG, await _id(factory, STAFF_TG), "sales_reports", False
        )
        enabled = [view.enabled for view in grid.grants if view.feature_key == "sales_reports"]
        assert enabled == [False]
        assert FeatureKey.SALES_REPORTS not in await service.allowed_for(staff)
        with pytest.raises(FeatureAccessDenied):
            async with factory() as session:
                await require_feature(session, STAFF_TG, FeatureKey.SALES_REPORTS)

    _scenario(scenario)


def test_one_user_toggle_never_changes_another_user() -> None:
    async def scenario(factory: Factory) -> None:
        service = FeaturePermissionService(factory)
        await service.set_for_actor(ADMIN_TG, await _id(factory, STAFF_TG), "image_search", False)
        assert (await service.grants_for(await _user(factory, STAFF_TG)))["image_search"] is False
        other = await service.grants_for(await _user(factory, OTHER_TG))
        assert other["image_search"] is baseline_grants()["image_search"]

    _scenario(scenario)


def test_every_toggle_is_audited_once_and_a_repeat_writes_nothing_new() -> None:
    async def scenario(factory: Factory) -> None:
        service = FeaturePermissionService(factory)
        staff = await _id(factory, STAFF_TG)
        await service.set_for_actor(ADMIN_TG, staff, "reminders", False)
        await service.set_for_actor(ADMIN_TG, staff, "reminders", False)
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(AuditEvent).where(
                            AuditEvent.entity_type == "user_feature",
                            AuditEvent.action == "USER_FEATURE_DISABLED",
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].event_metadata is not None
        assert rows[0].event_metadata["feature_key"] == "reminders"
        assert rows[0].event_metadata["target_user_id"] == staff

    _scenario(scenario)


def test_only_an_administrator_may_read_or_change_the_grid() -> None:
    async def scenario(factory: Factory) -> None:
        service = FeaturePermissionService(factory)
        staff = await _id(factory, STAFF_TG)
        other = await _id(factory, OTHER_TG)
        with pytest.raises(AuthorizationDenied):
            await service.grid_for_actor(STAFF_TG, staff)
        with pytest.raises(AuthorizationDenied):
            await service.set_for_actor(STAFF_TG, other, "orders", True)
        with pytest.raises(UnknownFeatureTarget):
            await service.grid_for_actor(ADMIN_TG, 999_999_999)

    _scenario(scenario)


def test_a_purged_user_takes_its_grant_rows_with_it() -> None:
    async def scenario(factory: Factory) -> None:
        staff = await _id(factory, STAFF_TG)
        assert await _count(factory, staff) == len(FEATURE_KEYS)
        async with factory.begin() as session:
            await session.execute(delete(User).where(User.id == staff))
        assert await _count(factory, staff) == 0

    _scenario(scenario)
