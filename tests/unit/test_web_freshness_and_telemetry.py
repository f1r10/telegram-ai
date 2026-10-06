"""Web freshness scoping, retry and telemetry: no more silent 'nothing found' claims."""

import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from office_assistant.infrastructure.database.models import User
from office_assistant.internal_evidence.models import InternalAnswer, RoutingCategory
from office_assistant.web_evidence.models import (
    WebEvidenceUnavailable,
    WebProviderUnavailable,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from office_assistant.web_evidence.normalization import (
    is_fresh_query,
    is_time_sensitive_query,
)
from office_assistant.web_evidence.service import WEB_NOT_FOUND_AZ, WebEvidenceService

SERVICE_LOGGER = "office_assistant.web_evidence.service"
NOW = datetime.now(UTC)


def _actor() -> User:
    return cast(User, SimpleNamespace(id=1))


def _response(
    results: list[WebSearchResult], *, provider: str = "searxng", cache_hit: bool = False
) -> WebSearchResponse:
    return WebSearchResponse(
        provider=provider,
        results=results,
        retrieved_at=NOW,
        cache_hit=cache_hit,
    )


def _hit(
    title: str = "Neft qiymətləri 2026", url: str = "https://example.com/oil"
) -> WebSearchResult:
    return WebSearchResult(
        title=title,
        url=url,
        domain="example.com",
        snippet="İctimai qiymət məlumatı.",
        retrieved_at=NOW,
        provider="searxng",
        rank=1,
    )


def _service(search: object, fetcher: object | None = None) -> WebEvidenceService:
    internal = InternalAnswer(answer="Daxili cavab.", route=RoutingCategory.INTERNAL_DOCUMENT)
    service = WebEvidenceService(
        None,  # type: ignore[arg-type]
        search,  # type: ignore[arg-type]
        fetcher or SimpleNamespace(fetch=AsyncMock(side_effect=RuntimeError("skip"))),  # type: ignore[arg-type]
        SimpleNamespace(generate=AsyncMock()),  # type: ignore[arg-type]
        SimpleNamespace(answer=AsyncMock(return_value=internal)),  # type: ignore[arg-type]
    )
    service._persist_turn = AsyncMock()  # type: ignore[method-assign]
    return service


@pytest.mark.parametrize(
    ("query", "fresh"),
    [
        ("Neft qiyməti neçədir?", False),
        ("KA45 məhsulu üçün zəmanət nə qədərdir?", False),
        ("bu gün neft qiyməti", True),
        ("hazırda kurs nə qədərdir?", True),
        ("son xəbər nə var?", True),
        ("latest price of crude oil", True),
    ],
)
def test_only_explicit_freshness_wording_narrows_the_window(query: str, fresh: bool) -> None:
    assert is_fresh_query(query) is fresh


@pytest.mark.parametrize(
    ("query", "sensitive"),
    [
        ("Neft qiyməti neçədir?", True),
        ("bu gün neft qiyməti", True),
        ("Zəmanət şərtləri nələri əhatə edir?", False),
    ],
)
def test_time_sensitivity_still_shortens_the_cache_ttl(query: str, sensitive: bool) -> None:
    assert is_time_sensitive_query(query) is sensitive


def test_price_question_does_not_force_a_one_day_window() -> None:
    search = SimpleNamespace(search=AsyncMock(return_value=_response([_hit()])))
    service = _service(search)
    evidence = asyncio.run(
        service._collect("Neft qiyməti neçədir?")  # pyright: ignore[reportPrivateUsage]
    )[0]
    request = search.search.await_args.args[0]
    assert isinstance(request, WebSearchRequest)
    assert request.freshness is None
    assert len(evidence) == 1


def test_empty_fresh_search_is_retried_without_a_time_window() -> None:
    search = SimpleNamespace(search=AsyncMock(side_effect=[_response([]), _response([_hit()])]))
    service = _service(search)
    evidence, response, _ = asyncio.run(
        service._collect("bu gün neft qiyməti")  # pyright: ignore[reportPrivateUsage]
    )
    windows = [call.args[0].freshness for call in search.search.await_args_list]
    assert windows == ["day", None]
    assert [item.title for item in evidence] == ["Neft qiymətləri 2026"]
    assert response.results


def test_non_fresh_search_is_never_retried() -> None:
    search = SimpleNamespace(search=AsyncMock(return_value=_response([])))
    service = _service(search)
    evidence, _, _ = asyncio.run(
        service._collect("Zəmanət müddəti neçə aydır?")  # pyright: ignore[reportPrivateUsage]
    )
    assert evidence == []
    assert search.search.await_count == 1


def test_provider_outage_degrades_as_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    search = SimpleNamespace(search=AsyncMock(side_effect=WebProviderUnavailable("engines down")))
    service = _service(search)
    with caplog.at_level(logging.INFO, logger=SERVICE_LOGGER):
        with pytest.raises(WebEvidenceUnavailable):
            asyncio.run(service.answer_internet(_actor(), "bu gün qiymət"))
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert WEB_NOT_FOUND_AZ not in text
    assert "produced no evidence" not in text


def test_healthy_empty_search_is_logged_as_no_evidence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    search = SimpleNamespace(search=AsyncMock(return_value=_response([])))
    service = _service(search)
    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        result = asyncio.run(service.answer_internet(_actor(), "Zəmanət müddəti"))
    assert result.answer == WEB_NOT_FOUND_AZ
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Web search produced no evidence | mode=internet provider=searxng" in text


def test_combined_outage_logs_the_internal_side(caplog: pytest.LogCaptureFixture) -> None:
    search = SimpleNamespace(search=AsyncMock(return_value=_response([])))
    service = _service(search)
    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        asyncio.run(service.answer_combined(_actor(), "Zəmanət müddəti"))
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "mode=combined" in text
    assert "internal_citations=" in text


def test_relevance_gate_drops_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    search = SimpleNamespace(search=AsyncMock(return_value=_response([_hit()])))
    service = _service(search)
    with caplog.at_level(logging.INFO, logger=SERVICE_LOGGER):
        asyncio.run(service._collect("KA45 qiyməti"))  # pyright: ignore[reportPrivateUsage]
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Web results dropped by relevance gate | dropped=1 kept=0 freshness=-" in text
