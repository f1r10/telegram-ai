"""Master Spec §21.3: SearXNG first, Tavily only when the first answer is not usable."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import AsyncMock

import pytest

from office_assistant.infrastructure.database.models import User
from office_assistant.internal_evidence.models import InternalAnswer, RoutingCategory
from office_assistant.web_evidence.cache import WebSearchService
from office_assistant.web_evidence.models import (
    ExternalWebEvidence,
    WebProviderUnavailable,
    WebSearchProvider,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from office_assistant.web_evidence.normalization import is_within_window
from office_assistant.web_evidence.service import WEB_UNAVAILABLE_AZ, WebEvidenceService

NOW = datetime.now(UTC)
SERVICE_LOGGER = "office_assistant.web_evidence.service"
CACHE_LOGGER = "office_assistant.web_evidence.cache"


def _hit(
    title: str = "Neft bazarı 2026 icmalası",
    url: str = "https://example.com/oil",
    *,
    provider: str = "searxng",
    published_at: datetime | None = None,
) -> WebSearchResult:
    return WebSearchResult(
        title=title,
        url=url,
        domain="example.com",
        snippet="İctimai məlumat.",
        published_at=published_at,
        retrieved_at=NOW,
        provider=provider,
        rank=1,
    )


def _response(results: list[WebSearchResult], provider: str = "searxng") -> WebSearchResponse:
    return WebSearchResponse(provider=provider, results=results, retrieved_at=NOW)


class _Provider:
    def __init__(
        self,
        name: str,
        outcome: WebSearchResponse | Exception,
    ) -> None:
        self.provider_name = name
        self.outcome = outcome
        self.names: list[str] = []
        self.calls = 0

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        self.calls += 1
        self.names.append(request.query)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _search_service(
    primary: _Provider,
    fallback: _Provider | None,
    *,
    cached: WebSearchResponse | None = None,
) -> WebSearchService:
    service = WebSearchService(
        cast(object, None),  # type: ignore[arg-type]
        cast(WebSearchProvider, primary),
        cast(WebSearchProvider | None, fallback),
    )
    service._read_cache = AsyncMock(return_value=cached)  # type: ignore[method-assign]
    service._write_cache = AsyncMock()  # type: ignore[method-assign]
    return service


def _irrelevant(result: WebSearchResult) -> bool:
    return "KA45" in result.title or "KA45" in result.snippet


def _service(search: object, internal: InternalAnswer) -> WebEvidenceService:
    return WebEvidenceService(
        None,  # type: ignore[arg-type]
        search,  # type: ignore[arg-type]
        SimpleNamespace(fetch=AsyncMock(side_effect=RuntimeError("skip"))),  # type: ignore[arg-type]
        SimpleNamespace(generate=AsyncMock()),  # type: ignore[arg-type]
        SimpleNamespace(answer=AsyncMock(return_value=internal)),  # type: ignore[arg-type]
    )


def _internal() -> InternalAnswer:
    return InternalAnswer(answer="Daxili cavab.", route=RoutingCategory.INTERNAL_DOCUMENT)


def _search(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(search=AsyncMock(**kwargs))  # type: ignore[arg-type]


def _evidence(service: WebEvidenceService, question: str) -> list[ExternalWebEvidence]:
    """Collection is the unit under test here, so the answer half of its result is dropped."""

    return asyncio.run(service._collect(question))[0]  # pyright: ignore[reportPrivateUsage]


def test_a_page_of_unusable_hits_sends_the_request_to_the_fallback() -> None:
    primary = _Provider("searxng", _response([_hit("Kafeel elanları"), _hit("Qızıl xəbəri")]))
    fallback = _Provider("tavily", _response([_hit("KA45 rəsmi səhifəsi")], provider="tavily"))

    result = asyncio.run(
        _search_service(primary, fallback).search(
            WebSearchRequest(query="KA45 qiyməti"), usable=_irrelevant
        )
    )

    assert (primary.calls, fallback.calls) == (1, 1)
    assert result.provider == "tavily"
    assert result.fallback_used is True
    assert fallback.names == ["KA45 qiyməti"]


def test_usable_primary_hits_never_reach_the_fallback() -> None:
    primary = _Provider("searxng", _response([_hit("KA45 kataloqu")]))
    fallback = _Provider("tavily", _response([_hit("KA45 rəsmi səhifəsi")], provider="tavily"))

    result = asyncio.run(
        _search_service(primary, fallback).search(
            WebSearchRequest(query="KA45 qiyməti"), usable=_irrelevant
        )
    )

    assert (primary.calls, fallback.calls) == (1, 0)
    assert result.fallback_used is False


def test_without_a_fallback_provider_unusable_hits_stay_an_empty_answer() -> None:
    primary = _Provider("searxng", _response([_hit("Kafeel elanları")]))

    result = asyncio.run(
        _search_service(primary, None).search(WebSearchRequest(query="KA45"), usable=_irrelevant)
    )

    assert result.provider == "searxng"
    assert result.fallback_used is False
    assert primary.calls == 1


def test_a_fallback_failure_is_not_reported_as_an_empty_index() -> None:
    primary = _Provider("searxng", _response([_hit("Kafeel elanları")]))
    fallback = _Provider("tavily", WebProviderUnavailable("Tavily is unavailable"))
    service = _search_service(primary, fallback)

    with pytest.raises(WebProviderUnavailable):
        asyncio.run(service.search(WebSearchRequest(query="KA45 qiyməti"), usable=_irrelevant))

    assert (primary.calls, fallback.calls) == (1, 1)


def test_both_providers_failing_surfaces_the_fallback_error() -> None:
    primary = _Provider("searxng", WebProviderUnavailable("SearXNG is unavailable"))
    fallback = _Provider("tavily", WebProviderUnavailable("Tavily is unavailable"))
    service = _search_service(primary, fallback)

    with pytest.raises(WebProviderUnavailable):
        asyncio.run(service.search(WebSearchRequest(query="neft qiyməti")))

    assert (primary.calls, fallback.calls) == (1, 1)


def test_a_cached_page_of_unusable_hits_is_searched_again() -> None:
    cached = _response([_hit("Kafeel elanları")])
    primary = _Provider("searxng", _response([_hit("KA45 kataloqu")]))
    fallback = _Provider("tavily", _response([_hit("KA45 rəsmi")], provider="tavily"))

    result = asyncio.run(
        _search_service(primary, fallback, cached=cached).search(
            WebSearchRequest(query="KA45 qiyməti"), usable=_irrelevant
        )
    )

    assert result.cache_hit is False
    assert primary.calls == 1


def test_a_cache_hit_from_the_fallback_keeps_reporting_the_fallback() -> None:
    cached = _response([_hit("KA45 rəsmi", provider="tavily")], provider="tavily")
    primary = _Provider("searxng", _response([_hit()]))

    result = asyncio.run(
        _search_service(primary, None, cached=cached).search(WebSearchRequest(query="KA45"))
    )

    assert result.cache_hit is True
    assert result.fallback_used is True
    assert primary.calls == 0


def test_a_cache_hit_from_the_primary_is_not_a_fallback() -> None:
    cached = _response([_hit()])
    primary = _Provider("searxng", _response([_hit()]))

    result = asyncio.run(
        _search_service(primary, None, cached=cached).search(WebSearchRequest(query="neft"))
    )

    assert result.cache_hit is True
    assert result.fallback_used is False


def test_fallback_engagement_is_logged_without_the_answer_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    primary = _Provider("searxng", _response([_hit("Kafeel elanları")]))
    fallback = _Provider("tavily", _response([_hit("KA45 rəsmi")], provider="tavily"))
    service = _search_service(primary, fallback)

    with caplog.at_level(logging.INFO, logger=CACHE_LOGGER):
        asyncio.run(service.search(WebSearchRequest(query="KA45 sənədin adı"), usable=_irrelevant))

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Web fallback engaged | reason=no_usable_results primary=searxng raw_results=1" in text
    assert "sənədin adı" not in text


@pytest.mark.parametrize(
    ("age_days", "freshness", "expected"),
    [
        (0, "day", True),
        (2, "day", False),
        (40, "month", False),
        (10, "month", True),
        (400, "year", False),
        (400, None, True),
        (None, "day", True),
    ],
)
def test_only_hits_dated_inside_the_window_count_as_fresh(
    age_days: int | None,
    freshness: Literal["day", "month", "year"] | None,
    expected: bool,
) -> None:
    published = None if age_days is None else NOW - timedelta(days=age_days)

    assert is_within_window(published, freshness, now=NOW) is expected


def test_a_publication_date_without_a_timezone_is_read_as_utc() -> None:
    naive_month_old = (NOW - timedelta(days=30)).replace(tzinfo=None)

    assert is_within_window(naive_month_old, "day", now=NOW) is False
    assert is_within_window(naive_month_old, "month", now=NOW) is True


def test_the_gate_reaches_the_search_layer_as_usability() -> None:
    search: SimpleNamespace = _search(
        return_value=_response([_hit("Kafeel elanları"), _hit("KA45 kataloqu")])
    )
    service = _service(search, _internal())

    evidence = _evidence(service, "KA45 qiyməti")

    usable: Callable[[WebSearchResult], bool] = search.search.await_args.kwargs["usable"]
    assert usable(_hit("KA45 kataloqu")) is True
    assert usable(_hit("Kafeel elanları")) is False
    assert [item.title for item in evidence] == ["KA45 kataloqu"]


def test_a_stale_hit_in_a_fresh_window_widens_the_search_window() -> None:
    stale = _hit("Neft bazarı icmalası", published_at=NOW - timedelta(days=30))
    search: SimpleNamespace = _search(
        side_effect=[
            _response([stale]),
            _response([_hit("Neft bazarı bu gün", provider="tavily")], provider="tavily"),
        ]
    )
    service = _service(search, _internal())

    evidence = _evidence(service, "bu gün neft qiyməti")

    windows = [call.args[0].freshness for call in search.search.await_args_list]
    assert windows == ["day", None]
    assert [item.title for item in evidence] == ["Neft bazarı bu gün"]


def test_combined_keeps_the_internal_answer_when_web_search_is_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    search: SimpleNamespace = _search(side_effect=WebProviderUnavailable("providers down"))
    service = _service(search, _internal())
    persisted = AsyncMock()
    service._persist_turn = persisted  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        result = asyncio.run(service.answer_combined(cast(User, SimpleNamespace(id=1)), "neft"))

    assert result.answer == f"Daxili cavab.\n\n{WEB_UNAVAILABLE_AZ}"
    assert result.citations == ()
    assert "Web search unavailable for combined answer" in caplog.text
    persisted.assert_awaited_once()
