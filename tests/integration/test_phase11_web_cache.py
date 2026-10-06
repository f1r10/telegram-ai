import asyncio
import os
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy import delete

from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import WebSearchCache
from office_assistant.infrastructure.database.session import create_session_factory
from office_assistant.web_evidence.cache import WebSearchService
from office_assistant.web_evidence.models import (
    WebSearchProvider,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from office_assistant.web_evidence.providers import SearXNGWebSearchProvider

pytestmark = pytest.mark.integration


class CountingProvider:
    provider_name = "searxng"

    def __init__(self) -> None:
        self.calls = 0

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        self.calls += 1
        now = datetime.now(UTC)
        return WebSearchResponse(
            provider=self.provider_name,
            retrieved_at=now,
            results=[
                WebSearchResult(
                    title="Python",
                    url="https://python.org/",
                    domain="python.org",
                    snippet="Python language",
                    retrieved_at=now,
                    provider=self.provider_name,
                    rank=1,
                )
            ],
        )


def test_postgres_cache_write_read_and_parameter_isolation() -> None:
    asyncio.run(_run_cache_test())


async def _run_cache_test() -> None:
    engine = create_database_engine(load_settings())
    factory = create_session_factory(engine)
    provider = CountingProvider()
    service = WebSearchService(factory, cast(WebSearchProvider, provider))
    try:
        async with factory.begin() as session:
            await session.execute(delete(WebSearchCache))
        stable = WebSearchRequest(query="Python 3.14", max_results=5)
        fresh = WebSearchRequest(query="Python 3.14", max_results=5, freshness="day")
        first = await service.search(stable)
        second = await service.search(stable)
        third = await service.search(fresh)
        assert not first.cache_hit
        assert second.cache_hit
        assert not third.cache_hit
        assert provider.calls == 2
    finally:
        await engine.dispose()


def test_real_local_searxng_search_and_cache() -> None:
    base_url = os.getenv("SEARXNG_INTEGRATION_URL")
    if not base_url:
        pytest.skip("Set SEARXNG_INTEGRATION_URL for the real local integration test")
    asyncio.run(_run_real_searxng_test(base_url))


async def _run_real_searxng_test(base_url: str) -> None:
    engine = create_database_engine(load_settings())
    factory = create_session_factory(engine)
    provider = SearXNGWebSearchProvider(base_url)
    service = WebSearchService(factory, provider)
    try:
        async with factory.begin() as session:
            await session.execute(delete(WebSearchCache))
        request = WebSearchRequest(query="Python 3.14", max_results=3, language="en")
        first = await service.search(request)
        second = await service.search(request)
        assert first.results
        assert all(item.url.startswith(("http://", "https://")) for item in first.results)
        assert not first.cache_hit
        assert second.cache_hit
        assert [item.url for item in second.results] == [item.url for item in first.results]
    finally:
        await provider.close()
        await engine.dispose()
