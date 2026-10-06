import pytest

from office_assistant.core.settings import load_settings


@pytest.fixture(autouse=True)
def require_disposable_database(request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("integration") is None:  # pyright: ignore[reportUnknownMemberType]
        return
    settings = load_settings()
    if settings.postgres_host and settings.postgres_password:
        if not (settings.postgres_db or "").endswith("_test"):
            pytest.fail(
                "Integration tests delete data. Use a disposable POSTGRES_DB ending in _test."
            )
