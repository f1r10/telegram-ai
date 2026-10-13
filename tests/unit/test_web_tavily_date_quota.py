"""Web-search date and quota parsing based on Tavily's documented API contracts."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from office_assistant.web_evidence.models import WebProviderQuotaError, WebSearchRequest
from office_assistant.web_evidence.providers import (
    TavilyWebSearchProvider,
    _date,  # pyright: ignore[reportPrivateUsage]
)


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        ("Tue, 11 Mar 2025 17:00:00 GMT", datetime(2025, 3, 11, 17, 0, tzinfo=UTC)),
        ("2025-03-11T17:00:00Z", datetime(2025, 3, 11, 17, 0, tzinfo=UTC)),
        ("2025-03-11T17:00:00", datetime(2025, 3, 11, 17, 0, tzinfo=UTC)),
        ("not a timestamp", None),
        (None, None),
    ],
)
def test_published_date_accepts_iso_and_tavily_rfc_2822(
    published: str | None, expected: datetime | None
) -> None:
    assert _date(published) == expected


@pytest.mark.parametrize("status", [429, 432, 433])
def test_documented_tavily_limit_responses_are_quota_errors(status: int) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status, json={"detail": "mock limit response"}, request=request
            )
        )
    )
    provider = TavilyWebSearchProvider("test-placeholder", client=client)
    try:
        with pytest.raises(WebProviderQuotaError):
            asyncio.run(provider.search(WebSearchRequest(query="məhsul bazar qiyməti")))
    finally:
        asyncio.run(client.aclose())


def test_tavily_rfc_date_propagates_to_search_hit() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Public article",
                            "url": "https://example.org/article",
                            "content": "Public information only.",
                            "published_date": "Tue, 11 Mar 2025 17:00:00 GMT",
                        }
                    ]
                },
                request=request,
            )
        )
    )
    provider = TavilyWebSearchProvider("test-placeholder", client=client)
    try:
        answer = asyncio.run(provider.search(WebSearchRequest(query="public product price")))
    finally:
        asyncio.run(client.aclose())
    assert len(answer.results) == 1
    assert answer.results[0].published_at == datetime(2025, 3, 11, 17, 0, tzinfo=UTC)
