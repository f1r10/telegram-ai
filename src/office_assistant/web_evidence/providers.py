"""SearXNG primary and Tavily fallback provider adapters."""

from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit

import httpx

from office_assistant.web_evidence.models import (
    WebProviderAuthError,
    WebProviderMalformed,
    WebProviderQuotaError,
    WebProviderUnavailable,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from office_assistant.web_evidence.normalization import canonical_url


def _domain(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().rstrip(".")


def _date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _usable_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        normalized = canonical_url(value)
    except ValueError:
        return None
    parts = urlsplit(normalized)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    return normalized


class SearXNGWebSearchProvider:
    provider_name = "searxng"

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 8.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        params: dict[str, str | int] = {
            "q": request.query,
            "format": "json",
            "categories": "general",
            "safesearch": 1,
        }
        if request.language:
            params["language"] = request.language
        if request.freshness:
            params["time_range"] = request.freshness
        try:
            response = await self._client.get(f"{self.base_url}/search", params=params)
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as error:
            raise WebProviderUnavailable("SearXNG is unavailable") from error
        try:
            payload = cast(dict[str, object], response.json())
            raw_results = payload.get("results")
            if not isinstance(raw_results, list):
                raise ValueError("results is not a list")
        except (ValueError, TypeError) as error:
            raise WebProviderMalformed("SearXNG returned malformed JSON") from error
        now = datetime.now(UTC)
        results: list[WebSearchResult] = []
        seen: set[str] = set()
        for raw in cast(list[object], raw_results):
            if not isinstance(raw, dict):
                continue
            item = cast(dict[str, object], raw)
            url = _usable_url(item.get("url"))
            title = item.get("title")
            domain = _domain(url or "")
            if (
                not url
                or not domain
                or not isinstance(title, str)
                or not title.strip()
                or url in seen
            ):
                continue
            if request.allowed_domains and not any(
                domain == allowed.casefold() or domain.endswith(f".{allowed.casefold()}")
                for allowed in request.allowed_domains
            ):
                continue
            if any(
                domain == blocked.casefold() or domain.endswith(f".{blocked.casefold()}")
                for blocked in request.blocked_domains
            ):
                continue
            seen.add(url)
            results.append(
                WebSearchResult(
                    title=title.strip(),
                    url=url,
                    domain=domain,
                    snippet=str(item.get("content") or "").strip()[:5000],
                    published_at=_date(item.get("publishedDate") or item.get("published_at")),
                    retrieved_at=now,
                    provider=self.provider_name,
                    rank=len(results) + 1,
                )
            )
            if len(results) >= request.max_results:
                break
        return WebSearchResponse(provider=self.provider_name, results=results, retrieved_at=now)


class TavilyWebSearchProvider:
    provider_name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        body: dict[str, object] = {
            "query": request.query,
            "search_depth": "basic",
            "max_results": request.max_results,
            "topic": "general",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_published_date": True,
        }
        if request.freshness:
            body["time_range"] = request.freshness
        if request.allowed_domains:
            body["include_domains"] = request.allowed_domains
        if request.blocked_domains:
            body["exclude_domains"] = request.blocked_domains
        if request.language:
            body["language"] = request.language
        if not self.api_key:
            raise WebProviderAuthError("Tavily API key is not configured")
        try:
            response = await self._client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            raise WebProviderUnavailable("Tavily is unavailable") from error
        if response.status_code in {401, 403}:
            detail = response.text.casefold()
            if "limit" in detail or "quota" in detail or "plan" in detail:
                raise WebProviderQuotaError("Tavily quota is unavailable")
            raise WebProviderAuthError("Tavily authentication failed")
        if response.status_code == 429:
            raise WebProviderQuotaError("Tavily rate limit reached")
        if response.status_code >= 500:
            raise WebProviderUnavailable("Tavily is unavailable")
        try:
            response.raise_for_status()
            payload = cast(dict[str, object], response.json())
            raw_results = payload.get("results")
            if not isinstance(raw_results, list):
                raise ValueError("results is not a list")
        except (httpx.HTTPStatusError, ValueError, TypeError) as error:
            raise WebProviderMalformed("Tavily returned malformed data") from error
        now = datetime.now(UTC)
        results: list[WebSearchResult] = []
        seen: set[str] = set()
        for raw in cast(list[object], raw_results):
            if not isinstance(raw, dict):
                continue
            item = cast(dict[str, object], raw)
            url = _usable_url(item.get("url"))
            title = item.get("title")
            if not url or url in seen or not isinstance(title, str) or not title.strip():
                continue
            seen.add(url)
            results.append(
                WebSearchResult(
                    title=title.strip(),
                    url=url,
                    domain=_domain(url),
                    snippet=str(item.get("content") or "").strip()[:5000],
                    published_at=_date(item.get("published_date")),
                    retrieved_at=now,
                    provider=self.provider_name,
                    rank=len(results) + 1,
                )
            )
            if len(results) >= request.max_results:
                break
        return WebSearchResponse(provider=self.provider_name, results=results, retrieved_at=now)
