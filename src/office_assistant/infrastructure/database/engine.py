"""SQLAlchemy async engine construction and lifecycle."""

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from office_assistant.core.settings import Settings
from office_assistant.infrastructure.database.errors import DatabaseConfigurationError


def build_database_url(settings: Settings) -> URL:
    """Build a credential-safe SQLAlchemy URL from typed settings."""

    required = {
        "POSTGRES_DB": settings.postgres_db,
        "POSTGRES_USER": settings.postgres_user,
        "POSTGRES_HOST": settings.postgres_host,
    }
    missing = [name for name, value in required.items() if not value]
    if settings.postgres_password is None:
        missing.append("POSTGRES_PASSWORD")
    if missing:
        raise DatabaseConfigurationError(
            f"Missing required database settings: {', '.join(missing)}"
        )

    assert settings.postgres_password is not None
    return URL.create(
        drivername="postgresql+asyncpg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


def create_database_engine(settings: Settings) -> AsyncEngine:
    """Create an engine without opening a connection during import or construction."""

    return create_async_engine(
        build_database_url(settings),
        echo=settings.postgres_echo,
        pool_size=settings.postgres_pool_size,
        max_overflow=settings.postgres_max_overflow,
        pool_timeout=settings.postgres_pool_timeout,
        # A restarted postgres leaves sockets in this pool that the server already closed.
        pool_pre_ping=True,
        # Recycle bounds how long an unverified socket may sit idle before it is replaced.
        pool_recycle=settings.postgres_pool_recycle_seconds,
    )
