"""Bounded, SSRF-aware public web page retrieval."""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from trafilatura import extract

from office_assistant.web_evidence.models import UnsafeWebURL, WebFetchError
from office_assistant.web_evidence.normalization import canonical_url

Resolver = Callable[[str, int], Awaitable[list[str]]]
_BLOCKED_HOSTS = {
    "localhost",
    "postgres",
    "qdrant",
    "searxng",
    "app",
    "worker",
    "host.docker.internal",
    "gateway.docker.internal",
    "metadata.google.internal",
}
_ALLOWED_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}


async def resolve_public(host: str, port: int) -> list[str]:
    try:
        records = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise UnsafeWebURL("Host adı həll edilə bilmədi") from error
    return list(dict.fromkeys(str(record[4][0]) for record in records))


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global and not any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


async def validate_public_url(url: str, resolver: Resolver = resolve_public) -> str:
    try:
        normalized = canonical_url(url)
        parts = urlsplit(normalized)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as error:
        raise UnsafeWebURL("URL etibarsızdır") from error
    host = (parts.hostname or "").casefold().rstrip(".")
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
        raise UnsafeWebURL("Yalnız ictimai HTTP/HTTPS URL-ləri qəbul edilir")
    if host in _BLOCKED_HOSTS or host.endswith((".local", ".internal", ".localhost")):
        raise UnsafeWebURL("Daxili host bloklanıb")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        addresses = await resolver(host, port)
        if not addresses or any(not _is_public_address(item) for item in addresses):
            raise UnsafeWebURL("Host ictimai şəbəkəyə aid deyil") from None
    else:
        if not _is_public_address(str(literal)):
            raise UnsafeWebURL("Şəxsi və ya xüsusi IP bloklanıb")
    return normalized


@dataclass(frozen=True, slots=True)
class FetchedWebContent:
    url: str
    text: str
    content_type: str
    bytes_read: int
    truncated: bool


class WebContentFetcher:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_bytes: int = 2 * 1024 * 1024,
        max_redirects: int = 3,
        max_text_chars: int = 14000,
        resolver: Resolver = resolve_public,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.max_text_chars = max_text_chars
        self.resolver = resolver
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0)),
            follow_redirects=False,
            headers={"User-Agent": "OfficeAssistantWebEvidence/1.0"},
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch(self, url: str) -> FetchedWebContent:
        current = url
        for redirect_count in range(self.max_redirects + 1):
            current = await validate_public_url(current, self.resolver)
            try:
                request = self._client.build_request("GET", current)
                response = await self._client.send(request, stream=True)
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                raise WebFetchError("Səhifə əldə edilə bilmədi") from error
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise WebFetchError("Yönləndirmə ünvanı yoxdur")
                    if redirect_count >= self.max_redirects:
                        raise WebFetchError("Yönləndirmə limiti aşıldı")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].casefold()
                if content_type not in _ALLOWED_TYPES:
                    raise WebFetchError("Dəstəklənməyən səhifə məzmun növü")
                length = response.headers.get("content-length")
                if length and int(length) > self.max_bytes:
                    raise WebFetchError("Səhifə ölçü limitini aşır")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self.max_bytes:
                        raise WebFetchError("Səhifə ölçü limitini aşır")
            except (httpx.HTTPStatusError, ValueError) as error:
                raise WebFetchError("Səhifə cavabı qəbul edilmədi") from error
            finally:
                await response.aclose()
            decoded = bytes(body).decode(response.encoding or "utf-8", errors="replace")
            if content_type in {"text/html", "application/xhtml+xml"}:
                text = await asyncio.to_thread(
                    extract,
                    decoded,
                    url=current,
                    include_comments=False,
                    include_tables=True,
                    favor_precision=True,
                )
                readable = (text or "").strip()
            else:
                readable = decoded.strip()
            if len(readable) < 40:
                raise WebFetchError("Səhifədən yetərli oxunaqlı mətn çıxarılmadı")
            truncated = len(readable) > self.max_text_chars
            return FetchedWebContent(
                current,
                readable[: self.max_text_chars],
                content_type,
                len(body),
                truncated,
            )
        raise WebFetchError("Yönləndirmə limiti aşıldı")
