import asyncio

import pytest
from sqlalchemy import delete, text

from office_assistant.core.settings import Settings, load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.health import check_database_health
from office_assistant.infrastructure.database.models import SystemMetadata
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


def test_database_connection_and_health() -> None:
    settings = _settings_or_skip()

    async def scenario() -> None:
        engine = create_database_engine(settings)
        try:
            health = await check_database_health(engine)
            assert health.is_healthy
            async with engine.connect() as connection:
                result = await connection.execute(text("SELECT 1"))
                assert result.scalar_one() == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_transaction_commit_and_rollback() -> None:
    settings = _settings_or_skip()
    committed_key = "phase1.integration.committed"
    rollback_key = "phase1.integration.rollback"

    async def scenario() -> None:
        engine = create_database_engine(settings)
        session_factory = create_session_factory(engine)
        try:
            async with session_factory.begin() as session:
                await session.execute(
                    delete(SystemMetadata).where(
                        SystemMetadata.key.in_([committed_key, rollback_key])
                    )
                )
            async with session_factory.begin() as session:
                session.add(SystemMetadata(key=committed_key, value="committed"))

            with pytest.raises(RuntimeError):
                async with session_factory.begin() as session:
                    session.add(SystemMetadata(key=rollback_key, value="rolled back"))
                    raise RuntimeError("test rollback")

            async with session_factory() as session:
                committed = await session.get(SystemMetadata, committed_key)
                rolled_back = await session.get(SystemMetadata, rollback_key)
                assert committed is not None
                assert committed.value == "committed"
                assert rolled_back is None

            async with session_factory.begin() as session:
                await session.execute(
                    delete(SystemMetadata).where(SystemMetadata.key == committed_key)
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_alembic_schema_is_at_head() -> None:
    settings = _settings_or_skip()

    async def scenario() -> None:
        engine = create_database_engine(settings)
        try:
            async with engine.connect() as connection:
                version = await connection.execute(text("SELECT version_num FROM alembic_version"))
                assert version.scalar_one() == "0014_add_reminders"
                table = await connection.execute(
                    text(
                        """
                        SELECT to_regclass('public.system_metadata')
                        """
                    )
                )
                assert table.scalar_one() == "system_metadata"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
