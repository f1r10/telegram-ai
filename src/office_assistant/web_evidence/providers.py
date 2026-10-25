"""SearXNG primary and Tavily fallback provider adapters."""

import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
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

logger = logging.getLogger(__name__)


def _domain(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().rstrip(".")


def _engine_failures(value: object) -> tuple[str, ...]:
    """Read SearXNG's ``unresponsive_engines`` pairs such as ``["duckduckgo", "CAPTCHA"]``."""
    if not isinstance(value, list):
        return ()
    failures: list[str] = []
    for entry in cast(list[object], value):
        if not isinstance(entry, (list, tuple)):
            continue
        parts = cast("list[object] | tuple[object, ...]", entry)
        if len(parts) != 2:
            continue
        failures.append(f"{str(parts[0]).casefold()}:{str(parts[1]).casefold()}")
    return tuple(failures)


def _date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        # Tavily often supplies RFC 2822 timestamps such as
        # "Tue, 11 Mar 2025 17:00:00 GMT" rather than ISO 8601.
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
        # Restrict the provider query itself, not just the returned hits:
        # a normal top-N search cannot discover lower-ranked site-specific pages.
        query = request.query
        if len(request.allowed_domains) == 1:
            domain = request.allowed_domains[0].casefold()
            if domain.replace(".", "").replace("-", "").isalnum():
                query = f"site:{domain} {request.query}"
        params: dict[str, str | int] = {
            "q": query,
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
        except (httpx.RequestError, httpx.HTTPStatusError) as error:
            raise WebProviderUnavailable("SearXNG is unavailable") from error
        try:
            decoded: object = response.json()
            if not isinstance(decoded, dict):
                raise ValueError("SearXNG response is not an object")
            payload = cast(dict[str, object], decoded)
            raw_results = payload.get("results")
            if not isinstance(raw_results, list):
                raise ValueError("results is not a list")
        except (ValueError, TypeError) as error:
            raise WebProviderMalformed("SearXNG returned malformed JSON") from error
        failures = _engine_failures(payload.get("unresponsive_engines"))
        items = cast(list[object], raw_results)
        returned = len(items)
        now = datetime.now(UTC)
        results: list[WebSearchResult] = []
        seen: set[str] = set()
        for raw in items:
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
        if not results and failures:
            # HTTP 200 with every engine broken is a technical outage, not an empty index.
            logger.warning(
                "SearXNG search failed | returned=%s kept=0 failed_engines=%s freshness=%s",
                returned,
                ",".join(failures),
                request.freshness or "-",
            )
            raise WebProviderUnavailable("SearXNG reported no working engine")
        logger.info(
            "SearXNG search completed | returned=%s kept=%s failed_engines=%s freshness=%s",
            returned,
            len(results),
            ",".join(failures) or "-",
            request.freshness or "-",
        )
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
        except httpx.RequestError as error:
            raise WebProviderUnavailable("Tavily is unavailable") from error
        if response.status_code in {401, 403}:
            detail = response.text.casefold()
            if "limit" in detail or "quota" in detail or "plan" in detail:
                raise WebProviderQuotaError("Tavily quota is unavailable")
            raise WebProviderAuthError("Tavily authentication failed")
        if response.status_code in {429, 432, 433}:
            raise WebProviderQuotaError("Tavily quota or rate limit reached")
        if response.status_code >= 500:
            raise WebProviderUnavailable("Tavily is unavailable")
        try:
            response.raise_for_status()
            decoded: object = response.json()
            if not isinstance(decoded, dict):
                raise ValueError("Tavily response is not an object")
            payload = cast(dict[str, object], decoded)
            raw_results = payload.get("results")
            if not isinstance(raw_results, list):
                raise ValueError("results is not a list")
        except (httpx.HTTPStatusError, ValueError, TypeError) as error:
            raise WebProviderMalformed("Tavily returned malformed data") from error
        items = cast(list[object], raw_results)
        returned = len(items)
        now = datetime.now(UTC)
        results: list[WebSearchResult] = []
        seen: set[str] = set()
        for raw in items:
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
        logger.info(
            "Tavily search completed | kept=%s of %s freshness=%s",
            len(results),
            returned,
            request.freshness or "-",
        )
        return WebSearchResponse(provider=self.provider_name, results=results, retrieved_at=now)
