"""Persistence, administrator-only edits, and atomic preference changes in CI Postgres."""

import asyncio

import pytest
from sqlalchemy import delete, select

from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    SystemMetadata,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.web_evidence.priority_sites import (
    PRIORITY_SITES_KEY,
    PrioritySitePermissionDenied,
    PrioritySitesService,
)

pytestmark = pytest.mark.integration
ADMIN_TG = 99998773
STAFF_TG = 99998774


def test_preferred_domains_survive_service_reload_and_reject_staff_writes() -> None:
    settings = load_settings()
    if settings.postgres_db != "office_assistant_test":
        pytest.skip("Preference writes are only allowed on disposable CI PostgreSQL")

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        preference = PrioritySitesService(factory)

        async def cleanup() -> None:
            async with factory.begin() as session:
                actor_ids = select(User.id).where(User.telegram_user_id.in_((ADMIN_TG, STAFF_TG)))
                await session.execute(
                    delete(AuditEvent).where(AuditEvent.actor_user_id.in_(actor_ids))
                )
                await session.execute(
                    delete(SystemMetadata).where(SystemMetadata.key == PRIORITY_SITES_KEY)
                )
                await session.execute(delete(User).where(User.id.in_(actor_ids)))

        try:
            await cleanup()
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
                    ]
                )
            assert await preference.list_domains() == ()
            assert await preference.update(ADMIN_TG, "add", "knatank.az") == ("knatank.az",)
            assert await preference.update(ADMIN_TG, "add", "https://www.knatank.az/") == (
                "knatank.az",
            )
            assert await preference.update(ADMIN_TG, "add", "example.org") == (
                "knatank.az",
                "example.org",
            )
            reloaded = PrioritySitesService(factory)
            assert await reloaded.list_domains() == ("knatank.az", "example.org")
            with pytest.raises(PrioritySitePermissionDenied):
                await reloaded.update(STAFF_TG, "remove", "knatank.az")
            assert await reloaded.list_domains() == ("knatank.az", "example.org")
            assert await reloaded.update(ADMIN_TG, "remove", "knatank.az") == ("example.org",)
            async with factory() as session:
                records = (
                    (
                        await session.execute(
                            select(AuditEvent).where(
                                AuditEvent.action.in_(
                                    ("WEB_PRIORITY_SITE_ADDED", "WEB_PRIORITY_SITE_REMOVED")
                                ),
                                AuditEvent.actor_user_id.in_(
                                    select(User.id).where(User.telegram_user_id == ADMIN_TG)
                                ),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(records) == 3
        finally:
            await cleanup()
            await engine.dispose()

    asyncio.run(scenario())
