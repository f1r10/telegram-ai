"""Web provider truthfulness: an engine outage is not reported as an empty index."""

import asyncio
import logging

import httpx
import pytest

from office_assistant.web_evidence.models import (
    WebProviderUnavailable,
    WebSearchRequest,
    WebSearchResponse,
)
from office_assistant.web_evidence.providers import (
    SearXNGWebSearchProvider,
    _engine_failures,  # pyright: ignore[reportPrivateUsage]
)

OUTAGE_PAYLOAD: dict[str, object] = {
    "results": [],
    "answers": [],
    "suggestions": [],
    "unresponsive_engines": [["duckduckgo", "CAPTCHA"], ["google", "access denied"]],
}

HEALTHY_EMPTY_PAYLOAD: dict[str, object] = {
    "results": [],
    "answers": [],
    "suggestions": [],
    "unresponsive_engines": [],
}


def _provider(payload: dict[str, object]) -> SearXNGWebSearchProvider:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    return SearXNGWebSearchProvider("http://search", client=client)


def _partial_failure_payload() -> dict[str, object]:
    return {
        "results": [{"title": "One", "url": "https://example.com/a", "content": "First"}],
        "unresponsive_engines": [["google", "access denied"]],
    }


def _search(payload: dict[str, object], query: str = "Neft bazarı qiymətləri") -> WebSearchResponse:
    provider = _provider(payload)
    try:
        return asyncio.run(provider.search(WebSearchRequest(query=query, freshness="day")))
    finally:
        asyncio.run(provider.close())


def test_every_engine_failed_is_a_technical_outage() -> None:
    with pytest.raises(WebProviderUnavailable):
        _search(OUTAGE_PAYLOAD)


def test_healthy_engines_without_hits_stay_an_empty_success() -> None:
    assert _search(HEALTHY_EMPTY_PAYLOAD).results == []


def test_partial_engine_failure_with_hits_is_a_success() -> None:
    assert [item.title for item in _search(_partial_failure_payload()).results] == ["One"]


def test_outage_logs_engines_but_never_the_query(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="office_assistant.web_evidence.providers"):
        with pytest.raises(WebProviderUnavailable):
            _search(OUTAGE_PAYLOAD, query="Neft bazarı qiymətləri")
    text = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "office_assistant.web_evidence.providers"
    )
    assert "duckduckgo:captcha" in text
    assert "google:access denied" in text
    assert "freshness=day" in text
    assert "Neft" not in text
    assert "qiymətləri" not in text


def test_healthy_empty_search_logs_counts(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="office_assistant.web_evidence.providers"):
        _search(HEALTHY_EMPTY_PAYLOAD)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "SearXNG search completed | returned=0 kept=0 failed_engines=- freshness=day" in text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([], ()),
        (None, ()),
        ("duckduckgo", ()),
        ([["one"]], ()),
        ([["DuckDuckGo", "CAPTCHA"]], ("duckduckgo:captcha",)),
        ([{"engine": "google"}], ()),
    ],
)
def test_engine_failure_shapes(value: object, expected: tuple[str, ...]) -> None:
    assert _engine_failures(value) == expected
