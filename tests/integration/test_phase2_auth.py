import asyncio

import pytest
from sqlalchemy import delete, select, update

from office_assistant.application.authentication import AuthenticationService, TelegramIdentity
from office_assistant.application.errors import AuthorizationDenied, InvalidStateTransition
from office_assistant.application.user_management import UserManagementService
from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AIConversation,
    AIMessage,
    AIUsage,
    AuditEvent,
    DocumentParseRun,
    FileObject,
    IngestionJob,
    IngestionSession,
    IngestionSessionItem,
    KnowledgeIndexJob,
    OutboxEvent,
    SystemMetadata,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory

pytestmark = pytest.mark.integration


def _settings_or_skip() -> Settings:
    settings = load_settings()
    if not all(
        (
            settings.postgres_db,
            settings.postgres_user,
            settings.postgres_password,
            settings.postgres_host,
        )
    ):
        pytest.skip("PostgreSQL integration settings are not configured")
    return settings


async def _reset_phase2_data(settings: Settings) -> None:
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory.begin() as session:
            await session.execute(delete(OutboxEvent))
            await session.execute(delete(AIUsage))
            await session.execute(delete(AIMessage))
            await session.execute(delete(AIConversation))
            await session.execute(delete(KnowledgeIndexJob))
            await session.execute(delete(DocumentParseRun))
            await session.execute(delete(IngestionJob))
            await session.execute(delete(IngestionSessionItem))
            await session.execute(delete(IngestionSession))
            await session.execute(delete(FileObject))
            await session.execute(delete(AuditEvent))
            await session.execute(delete(User))
            await session.execute(
                update(SystemMetadata)
                .where(SystemMetadata.key == "bootstrap.admin.initialized")
                .values(value="false")
            )
    finally:
        await engine.dispose()


def test_authentication_approval_rejection_and_disable_are_audited() -> None:
    settings = _settings_or_skip()
    asyncio.run(_reset_phase2_data(settings))

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        auth = AuthenticationService(factory, "phase2-access")
        users = UserManagementService(factory)
        try:
            assert (
                await auth.authenticate(
                    TelegramIdentity(899999, "wrong", "Wrong", None), "wrong-password"
                )
                is None
            )
            admin_result = await auth.authenticate(
                TelegramIdentity(900001, "admin", "Admin", None), "phase2-access"
            )
            assert admin_result is not None
            assert admin_result.outcome == "admin_created"
            assert admin_result.user.role == UserRole.ADMIN

            pending_result = await auth.authenticate(
                TelegramIdentity(900002, "user", "User", None), "phase2-access"
            )
            assert pending_result is not None
            assert pending_result.outcome == "pending"
            assert pending_result.user.status == UserStatus.PENDING
            assert pending_result.admin_telegram_ids == (900001,)

            approved = await users.approve(900001, pending_result.user.id)
            assert approved.status == UserStatus.ACTIVE
            assert approved.approved_by_user_id == admin_result.user.id
            repeated_approval = await users.approve(900001, approved.id)
            assert repeated_approval.id == approved.id

            disabled = await users.disable(900001, approved.id)
            assert disabled.status == UserStatus.DISABLED

            with pytest.raises(InvalidStateTransition):
                await users.disable(900001, admin_result.user.id)

            second_pending = await auth.authenticate(
                TelegramIdentity(900003, "other", "Other", None), "phase2-access"
            )
            assert second_pending is not None
            with pytest.raises(AuthorizationDenied):
                await users.approve(900002, second_pending.user.id)
            rejected = await users.reject(900001, second_pending.user.id)
            assert rejected.status == UserStatus.REJECTED

            async with factory() as session:
                actions = (
                    (await session.execute(select(AuditEvent.action).order_by(AuditEvent.id)))
                    .scalars()
                    .all()
                )
                assert list(actions) == [
                    "ADMIN_BOOTSTRAPPED",
                    "ACCESS_REQUEST_CREATED",
                    "USER_APPROVED",
                    "USER_DISABLED",
                    "ACCESS_REQUEST_CREATED",
                    "USER_REJECTED",
                ]
        finally:
            await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_reset_phase2_data(settings))


def test_bootstrap_concurrency_creates_only_one_admin() -> None:
    settings = _settings_or_skip()
    asyncio.run(_reset_phase2_data(settings))

    async def scenario() -> None:
        first_engine = create_database_engine(settings)
        second_engine = create_database_engine(settings)
        first_factory = create_session_factory(first_engine)
        second_factory = create_session_factory(second_engine)
        first_auth = AuthenticationService(first_factory, "phase2-access")
        second_auth = AuthenticationService(second_factory, "phase2-access")
        try:
            results = await asyncio.gather(
                first_auth.authenticate(
                    TelegramIdentity(910001, "one", "One", None), "phase2-access"
                ),
                second_auth.authenticate(
                    TelegramIdentity(910002, "two", "Two", None), "phase2-access"
                ),
            )
            outcomes = sorted(result.outcome for result in results if result is not None)
            assert outcomes == ["admin_created", "pending"]
            async with first_factory() as session:
                admins = (
                    (
                        await session.execute(
                            select(User).where(
                                User.role == UserRole.ADMIN, User.status == UserStatus.ACTIVE
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(admins) == 1
        finally:
            await first_engine.dispose()
            await second_engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_reset_phase2_data(settings))
