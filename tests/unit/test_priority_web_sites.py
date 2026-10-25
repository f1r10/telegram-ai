"""Preferred-domain search must be ordered, scoped, and degrade safely."""

import asyncio
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.web_evidence.cache import WebSearchService
from office_assistant.web_evidence.models import (
    WebSearchProvider,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from office_assistant.web_evidence.priority_sites import (
    InvalidPrioritySite,
    PrioritySitesService,
    normalize_priority_domain,
)

# Test harness overrides only cache I/O; no real DB or internet access.
# pyright: reportPrivateUsage=false


class FakePriorityDomains:
    def __init__(self, domains: tuple[str, ...]) -> None:
        self.domains = domains

    async def list_domains(self) -> tuple[str, ...]:
        return self.domains


class FakeProvider:
    provider_name = "searxng"

    def __init__(self, results_by_site: dict[str, list[str]]) -> None:
        self.results_by_site = results_by_site
        self.calls: list[str] = []

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        site = request.allowed_domains[0] if request.allowed_domains else "general"
        self.calls.append(site)
        now = datetime.now(UTC)
        urls = self.results_by_site.get(site, [])
        return WebSearchResponse(
            provider=self.provider_name,
            retrieved_at=now,
            results=[
                WebSearchResult(
                    title="Qalvanizli boru qiyməti",
                    url=url,
                    domain=url.split("/")[2],
                    snippet="Qalvanizli boru və ölçülər",
                    retrieved_at=now,
                    rank=index + 1,
                    provider=self.provider_name,
                )
                for index, url in enumerate(urls)
            ],
        )


class NoCacheSearch(WebSearchService):
    async def _read_cache(self, key: str) -> WebSearchResponse | None:
        del key
        return None

    async def _write_cache(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


def _service(
    primary: FakeProvider, domains: tuple[str, ...]
) -> WebSearchService:
    factory = cast(async_sessionmaker[AsyncSession], object())
    preferred = cast(PrioritySitesService, FakePriorityDomains(domains))
    return NoCacheSearch(factory, cast(WebSearchProvider, primary), priority_sites=preferred)


def test_priority_domain_validation_rejects_site_query_injection() -> None:
    assert normalize_priority_domain("HTTPS://WWW.KNATANK.AZ/") == "knatank.az"
    assert normalize_priority_domain("knatank.az") == "knatank.az"
    for raw in [
        "",
        "https://user@knatank.az",
        "knatank.az/test",
        "knatank.az?ref=x",
        "knatank.az site:example.org",
        "http://127.0.0.1",
        "http://localhost",
        "http://192.168.0.1",
        "http://knatank.az:8080",
        "http://knatank.az/#p",
    ]:
        with pytest.raises(InvalidPrioritySite):
            normalize_priority_domain(raw)


def test_priority_results_are_first_and_general_fallback_fills_sparse_hits() -> None:
    provider = FakeProvider(
        {
            "knatank.az": ["https://knatank.az/one"],
            "example.org": [],
            "general": ["https://general.example/a", "https://general.example/b"],
        }
    )
    response = asyncio.run(
        _service(provider, ("knatank.az", "example.org")).search(
            WebSearchRequest(query="qalvanizli boru", max_results=3),
            usable=lambda row: "boru" in row.snippet,
        )
    )
    assert provider.calls == ["knatank.az", "example.org", "general"]
    assert [item.url for item in response.results] == [
        "https://knatank.az/one",
        "https://general.example/a",
        "https://general.example/b",
    ]


def test_two_preferred_references_skip_general_search() -> None:
    provider = FakeProvider(
        {
            "knatank.az": ["https://knatank.az/a", "https://knatank.az/b"],
            "general": ["https://outside.example/z"],
        }
    )
    response = asyncio.run(
        _service(provider, ("knatank.az",)).search(
            WebSearchRequest(query="boru"), usable=lambda _: True
        )
    )
    assert provider.calls == ["knatank.az"]
    assert len(response.results) == 2


def test_provider_cannot_smuggle_wrong_domain_into_priority_output() -> None:
    provider = FakeProvider(
        {
            "knatank.az": ["https://wrong.example/x"],
            "general": ["https://generic.example/y"],
        }
    )
    response = asyncio.run(
        _service(provider, ("knatank.az",)).search(
            WebSearchRequest(query="boru"), usable=lambda _: True
        )
    )
    assert provider.calls == ["knatank.az", "general"]
    assert [item.url for item in response.results] == ["https://generic.example/y"]


def test_explicit_allowed_domain_is_never_overridden_by_preference() -> None:
    provider = FakeProvider({"separate.example": ["https://separate.example/a"]})
    result = asyncio.run(
        _service(provider, ("knatank.az",)).search(
            WebSearchRequest(query="boru", allowed_domains=["separate.example"])
        )
    )
    assert provider.calls == ["separate.example"]
    assert len(result.results) == 1
