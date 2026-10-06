"""PostgreSQL-backed web search cache and fallback orchestration."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.infrastructure.database.models import WebSearchCache
from office_assistant.web_evidence.models import (
    WebProviderError,
    WebSearchProvider,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResult,
)
from office_assistant.web_evidence.normalization import cache_key, is_fresh_query

logger = logging.getLogger(__name__)


class WebSearchService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        primary: WebSearchProvider,
        fallback: WebSearchProvider | None = None,
        *,
        stable_ttl_seconds: int = 86400,
        fresh_ttl_seconds: int = 900,
    ) -> None:
        self.session_factory = session_factory
        self.primary = primary
        self.fallback = fallback
        self.stable_ttl = timedelta(seconds=max(stable_ttl_seconds, 0))
        self.fresh_ttl = timedelta(seconds=max(fresh_ttl_seconds, 0))

    async def search(self, request: WebSearchRequest) -> WebSearchResponse:
        key, normalized = cache_key(request)
        cached = await self._read_cache(key)
        if cached is not None:
            return cached.model_copy(update={"cache_hit": True})
        fallback_used = False
        try:
            response = await self.primary.search(request)
        except WebProviderError:
            if self.fallback is None:
                raise
            fallback_used = True
            response = await self.fallback.search(request)
        else:
            if not response.results and self.fallback is not None:
                fallback_used = True
                response = await self.fallback.search(request)
        response = response.model_copy(update={"fallback_used": fallback_used})
        if response.results:
            ttl = (
                self.fresh_ttl
                if request.freshness or is_fresh_query(request.query)
                else self.stable_ttl
            )
            if ttl.total_seconds() > 0:
                await self._write_cache(key, normalized, request, response, ttl)
        return response

    async def _read_cache(self, key: str) -> WebSearchResponse | None:
        try:
            async with self.session_factory() as session:
                row = await session.scalar(
                    select(WebSearchCache).where(
                        WebSearchCache.query_key == key,
                        WebSearchCache.expires_at > datetime.now(UTC),
                    )
                )
                if row is None:
                    return None
                return WebSearchResponse(
                    provider=row.provider,
                    results=[WebSearchResult.model_validate(item) for item in row.results],
                    retrieved_at=row.created_at,
                )
        except Exception as error:
            logger.warning("Web cache read skipped | error_type=%s", type(error).__name__)
            return None

    async def _write_cache(
        self,
        key: str,
        normalized: str,
        request: WebSearchRequest,
        response: WebSearchResponse,
        ttl: timedelta,
    ) -> None:
        now = datetime.now(UTC)
        values = {
            "query_key": key,
            "normalized_query": normalized,
            "parameters": request.model_dump(mode="json", exclude={"query"}),
            "provider": response.provider,
            "results": [item.model_dump(mode="json") for item in response.results],
            "created_at": now,
            "expires_at": now + ttl,
        }
        try:
            async with self.session_factory.begin() as session:
                statement = insert(WebSearchCache).values(**values)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[WebSearchCache.query_key],
                        set_={name: value for name, value in values.items() if name != "query_key"},
                    )
                )
        except Exception as error:
            logger.warning("Web cache write skipped | error_type=%s", type(error).__name__)
