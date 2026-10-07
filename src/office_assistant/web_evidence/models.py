"""Provider-neutral web search and evidence contracts."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from office_assistant.ai.provider import TokenUsage
from office_assistant.internal_evidence.models import UnifiedCitation


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebSearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=1000)
    max_results: int = Field(default=6, ge=1, le=10)
    language: str | None = Field(default=None, max_length=20)
    freshness: Literal["day", "month", "year"] | None = None
    allowed_domains: list[str] = Field(default_factory=list, max_length=20)
    blocked_domains: list[str] = Field(default_factory=list, max_length=20)


class WebSearchResult(StrictModel):
    title: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1, max_length=4000)
    domain: str = Field(min_length=1, max_length=255)
    snippet: str = Field(default="", max_length=5000)
    published_at: datetime | None = None
    retrieved_at: datetime
    provider: str = Field(min_length=1, max_length=64)
    rank: int = Field(ge=1)


class WebSearchResponse(StrictModel):
    provider: str
    results: list[WebSearchResult]
    retrieved_at: datetime
    cache_hit: bool = False
    fallback_used: bool = False


class WebSearchProvider(Protocol):
    provider_name: str

    async def search(self, request: WebSearchRequest) -> WebSearchResponse: ...


# The caller owns this decision because usability depends on the question; the search layer only
# asks whether a page of hits is worth anything before it reaches for the fallback provider.
ResultUsability = Callable[[WebSearchResult], bool]


class ExternalWebEvidence(StrictModel):
    source_id: str
    title: str
    url: str
    domain: str
    snippet: str
    content: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime
    provider: str
    truncated: bool = False


class WebCitation(StrictModel):
    evidence_id: str
    number: int
    title: str
    url: str
    domain: str
    source_class: Literal["EXTERNAL_WEB"] = "EXTERNAL_WEB"

    @property
    def label(self) -> str:
        return f"İnternet — {self.domain} — {self.title} — {self.url}"


@dataclass(frozen=True, slots=True)
class WebAnswer:
    answer: str
    citations: tuple[WebCitation | UnifiedCitation, ...] = ()
    provider: str | None = None
    cache_hit: bool = False
    fallback_used: bool = False
    fetch_count: int = 0
    model: str | None = None
    usage: TokenUsage = TokenUsage()


class WebEvidenceUnavailable(RuntimeError):
    """All configured external search paths failed."""


class WebProviderError(RuntimeError):
    """Base provider failure eligible for controlled fallback."""


class WebProviderUnavailable(WebProviderError):
    pass


class WebProviderMalformed(WebProviderError):
    pass


class WebProviderAuthError(WebProviderError):
    pass


class WebProviderQuotaError(WebProviderError):
    pass


class UnsafeWebURL(ValueError):
    pass


class WebFetchError(RuntimeError):
    pass
