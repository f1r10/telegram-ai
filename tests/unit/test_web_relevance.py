import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from office_assistant.internal_evidence.models import (
    InternalAnswer,
    RoutingCategory,
    UnifiedCitation,
)
from office_assistant.web_evidence.models import WebSearchResponse, WebSearchResult
from office_assistant.web_evidence.normalization import (
    clean_answer_language,
    matches_requested_identifier,
)
from office_assistant.web_evidence.service import WEB_NOT_FOUND_AZ, WebEvidenceService


@pytest.mark.parametrize(
    ("query", "title", "snippet", "expected"),
    [
        ("KA45 qiyməti", "Kazakhstan news", "Currency rates", False),
        ("KA45 qiyməti", "KA450 offer", "Price 125 AZN", False),
        ("KA45 qiyməti", "XKA45 offer", "Price 125 AZN", False),
        ("KA45 qiyməti", "Offer", "ka-45 price 140 AZN", True),
        ("KA45 qiyməti", "KA 45 warranty", "", True),
        ("KA45 və KC99", "KC99", "Warranty", True),
        ("Python decorators", "Python", "Decorators guide", True),
    ],
)
def test_identifier_relevance(query: str, title: str, snippet: str, expected: bool) -> None:
    assert matches_requested_identifier(query, title, snippet) is expected


def test_answer_language_preserves_proper_names_urls_and_code() -> None:
    answer = (
        "STRUCTURED_AUTHORITATIVE: Дaxili qiymət 125 AZN [1]. "
        "INTERNAL_DOCUMENT: зiddiyyət var [2]. EXTERNAL_WEB: tapılmadı. "
        "Интерфакс https://example.com/Дaxili `Дaxili`"
    )
    result = clean_answer_language(answer)
    assert "Daxili biznes bazası: Daxili qiymət 125 AZN [1]" in result
    assert "Daxili sənəd: ziddiyyət var [2]" in result
    assert "İnternet: tapılmadı" in result
    assert "Интерфакс https://example.com/Дaxili `Дaxili`" in result


def test_irrelevant_cached_results_never_reach_fetch_model_or_citations() -> None:
    now = datetime.now(UTC)
    response = WebSearchResponse(
        provider="searxng",
        retrieved_at=now,
        cache_hit=True,
        results=[
            WebSearchResult(
                title="Currency rates",
                snippet="Kazakhstan news",
                url="https://example.com/",
                domain="example.com",
                provider="searxng",
                rank=1,
                retrieved_at=now,
            )
        ],
    )
    citation = UnifiedCitation(
        evidence_id="E1",
        number=1,
        source_class="STRUCTURED_AUTHORITATIVE",
        label="Biznes bazası",
    )
    internal = InternalAnswer(
        answer="Дaxili qiymət 125 AZN [1].",
        route=RoutingCategory.INTERNAL_HYBRID,
        citations=(citation,),
    )
    search = SimpleNamespace(search=AsyncMock(return_value=response))
    fetcher = SimpleNamespace(fetch=AsyncMock())
    model = SimpleNamespace(generate=AsyncMock())
    service = WebEvidenceService(
        None,  # type: ignore[arg-type]
        search,  # type: ignore[arg-type]
        fetcher,  # type: ignore[arg-type]
        model,  # type: ignore[arg-type]
        SimpleNamespace(answer=AsyncMock(return_value=internal)),  # type: ignore[arg-type]
    )
    service._persist_turn = AsyncMock()  # type: ignore[method-assign]
    actor = SimpleNamespace(id=1)
    result = asyncio.run(service.answer_combined(actor, "KA45 qiyməti"))  # type: ignore[arg-type]
    assert result.answer == f"Daxili qiymət 125 AZN [1].\n\nİnternet: {WEB_NOT_FOUND_AZ}"
    assert result.citations == (citation,)
    fetcher.fetch.assert_not_awaited()
    model.generate.assert_not_awaited()
    service._persist_turn.assert_awaited_once()  # pyright: ignore[reportPrivateUsage]
