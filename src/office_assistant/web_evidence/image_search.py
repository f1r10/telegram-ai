"""Consent-only, bounded Yandex reverse-image search from the owner's Colab prototype.

The unofficial cbirId protocol can change. CAPTCHA, HTTP 429 and anti-bot
responses are a STOP signal, never a reason to rotate identity or bypass.
Only explicit user-initiated images are uploaded to the fixed Yandex origin.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from time import monotonic
from typing import cast
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)
SEARCH_URL = "https://yandex.com/images/search"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_CARDS = 60
PAGE_SIZE = 5
CACHE_SECONDS = 86400
BLOCK_SECONDS = 900
_ID = re.compile(r"[a-zA-Z0-9/_-]{3,160}\Z")
_CHALLENGE = ("showcaptcha", "captcha__image", "smartcaptcha-form", "are you not a robot")


class ImageWebSearchUnavailable(RuntimeError):
    """Provider cannot be used: block, protocol change, timeout or malformed data."""


class ImageWebSearchBlocked(ImageWebSearchUnavailable):
    """A provider challenge must not be retried immediately."""


@dataclass(frozen=True, slots=True)
class ImageWebHit:
    title: str
    image_url: str
    source_url: str
    thumbnail_url: str
    domain: str
    priority: bool = False


@dataclass(frozen=True, slots=True)
class ImageWebResult:
    results: tuple[ImageWebHit, ...]
    provider_url: str
    blocked: bool = False
    cache_hit: bool = False


class _States(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.state_attributes: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"div", "script", "span", "li"}:
            return
        attrs_map = dict(attrs)
        state = attrs_map.get("data-state")
        if state and len(state) <= 1_000_000:
            self.state_attributes.append(state)


def _http_url(raw: object) -> str:
    if not isinstance(raw, str) or not 0 < len(raw) <= 2048:
        return ""
    value = raw.strip()
    if value.startswith("//"):
        value = "https:" + value
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if (
        parts.scheme not in {"https", "http"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.port not in {None, 80, 443}
    ):
        return ""
    return value.split("#", 1)[0]


def _domain(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().removeprefix("www.").rstrip(".")


def _approved_page(url: str) -> str:
    domain = _domain(url)
    if domain in {"yandex.com", "yandex.ru"} or domain.endswith((".yandex.com", ".yandex.ru")):
        return ""
    return url


def _item(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _url_at(value: object) -> str:
    obj = _item(value)
    return _http_url(obj.get("url")) if obj else _http_url(value)


def _decode_states(markup: str) -> list[dict[str, object]]:
    parser = _States()
    parser.feed(markup)
    found: list[dict[str, object]] = []
    for raw in parser.state_attributes[:400]:
        try:
            decoded: object = json.loads(unescape(raw))
        except (ValueError, TypeError):
            continue
        for candidate in (_item(decoded).get("initialState"), _item(decoded).get("state"), decoded):
            if isinstance(candidate, dict) and any(
                str(key).startswith("cbir") for key in candidate
            ):
                found.append(cast(dict[str, object], candidate))
                break
    return found


def _looks_image_link(url: str) -> bool:
    return urlsplit(url).path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"))


class _CardPairs(HTMLParser):
    """Fallback for Yandex li HTML: never join two different cards' links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.anchors: list[tuple[str, str, str]] = []
        self.current_url = ""
        self.current_title = ""
        self.current_thumb = ""
        self.hits: list[ImageWebHit] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag == "li":
            if self.depth == 0:
                self.anchors = []
            self.depth += 1
        if not self.depth:
            return
        if tag == "a":
            self.current_url = _http_url(attrs_map.get("href"))
            self.current_title = str(attrs_map.get("title") or "")[:180]
            self.current_thumb = ""
        if tag == "img":
            self.current_thumb = _http_url(attrs_map.get("data-src") or attrs_map.get("src"))
            self.current_title = str(attrs_map.get("alt") or self.current_title)[:180]

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.depth:
            if self.current_url:
                self.anchors.append((self.current_url, self.current_title, self.current_thumb))
            self.current_url = ""
            self.current_title = ""
            self.current_thumb = ""
        if tag != "li" or not self.depth:
            return
        self.depth -= 1
        if self.depth:
            return
        images = [entry for entry in self.anchors if _looks_image_link(entry[0])]
        pages = {
            _approved_page(entry[0]) for entry in self.anchors if not _looks_image_link(entry[0])
        }
        pages.discard("")
        if len(images) != 1 or len(pages) != 1:
            return
        page = next(iter(pages))
        image, title, thumb = images[0]
        self.hits.append(ImageWebHit(title or "Şəkil mənbəsi", image, page, thumb, _domain(page)))


def parse_yandex_results(markup: str, domains: tuple[str, ...] = ()) -> tuple[ImageWebHit, ...]:
    """Only cbirSites pairs from one JSON item; never invent a product source URL."""

    results: list[ImageWebHit] = []
    seen: set[tuple[str, str]] = set()
    for state in _decode_states(markup):
        for key in ("cbirSites", "cbirSitesList"):
            source = _item(state.get(key)).get("sites")
            if not isinstance(source, list):
                continue
            for candidate in cast(list[object], source)[:200]:
                entry = _item(candidate)
                page = _approved_page(_http_url(entry.get("url")))
                image = _url_at(entry.get("originalImage"))
                thumb = _url_at(entry.get("thumb"))
                if not image or not page or (image, page) in seen:
                    continue
                seen.add((image, page))
                domain = _domain(page)
                label = str(entry.get("title") or "Şəkil mənbəsi")[:180]
                priority = any(domain == d or domain.endswith("." + d) for d in domains)
                results.append(ImageWebHit(label, image, page, thumb, domain, priority))
                if len(results) >= MAX_CARDS:
                    break
    # HTML-only sites can carry a trustworthy pair within exactly one li.
    # Ambiguous cards are intentionally rejected, never joined by position.
    cards = _CardPairs()
    cards.feed(markup)
    for hit in cards.hits[:200]:
        if (hit.image_url, hit.source_url) in seen or len(results) >= MAX_CARDS:
            continue
        seen.add((hit.image_url, hit.source_url))
        priority = any(
            hit.domain == domain or hit.domain.endswith("." + domain) for domain in domains
        )
        results.append(
            ImageWebHit(
                hit.title,
                hit.image_url,
                hit.source_url,
                hit.thumbnail_url,
                hit.domain,
                priority,
            )
        )
    # Admin-preferred results are first, preserving provider rank within each group.
    return tuple(sorted(results, key=lambda candidate: not candidate.priority))


def _optimized_jpeg(raw: bytes) -> bytes:
    if not 0 < len(raw) <= MAX_UPLOAD_BYTES:
        raise ValueError("Image must be at most 4 MB")
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("Unsupported image type")
            if image.width * image.height > 20_000_000:
                raise ValueError("Image pixels exceed processing limit")
            rgb = ImageOps.exif_transpose(image).convert("RGB")
            rgb.thumbnail((1400, 1400))
            output = BytesIO()
            rgb.save(output, "JPEG", quality=86)
            return output.getvalue()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise ValueError("Invalid picture") from error


def _cbir_id(value: object) -> str:
    for block in cast(list[object], _item(value).get("blocks") or []):
        candidate = _item(_item(block).get("params")).get("cbirId")
        if isinstance(candidate, str) and _ID.fullmatch(candidate):
            return candidate
    return ""


def _challenge(body: str, status: int) -> bool:
    head = body[:9000].casefold()
    return status in {403, 429} or any(marker in head for marker in _CHALLENGE)


def _ranked(
    data: ImageWebResult, preferred_domains: tuple[str, ...], *, cache_hit: bool = False
) -> ImageWebResult:
    hits = []
    for hit in data.results:
        preferred = any(
            hit.domain == domain or hit.domain.endswith("." + domain)
            for domain in preferred_domains
        )
        hits.append(
            ImageWebHit(
                hit.title,
                hit.image_url,
                hit.source_url,
                hit.thumbnail_url,
                hit.domain,
                preferred,
            )
        )
    return ImageWebResult(
        tuple(sorted(hits, key=lambda hit: not hit.priority)),
        data.provider_url,
        cache_hit=cache_hit,
    )


class YandexReverseImageSearch:
    """Single-flight, bounded cache; circuit-breaker avoids hammering a challenged site."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        cache_ttl: int = CACHE_SECONDS,
    ) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=5.0),
            follow_redirects=False,
            headers={"Accept-Language": "az,en;q=0.8,ru;q=0.6"},
        )
        self._owns_client = client is None
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[float, ImageWebResult]] = {}
        self._blocked_until = 0.0
        self._single = asyncio.Lock()

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def search(
        self, image: bytes, *, preferred_domains: tuple[str, ...] = ()
    ) -> ImageWebResult:
        # Hash private query bytes without persisting them to a DB, logs or cache.
        digest = sha256(image).hexdigest()
        now = monotonic()
        cached = self._cache.get(digest)
        if cached is not None and now < cached[0]:
            return _ranked(cached[1], preferred_domains, cache_hit=True)
        if now < self._blocked_until:
            raise ImageWebSearchBlocked("Provider challenge; open Yandex directly")
        async with self._single:
            now = monotonic()
            cached = self._cache.get(digest)
            if cached is not None and now < cached[0]:
                return _ranked(cached[1], preferred_domains, cache_hit=True)
            jpeg = _optimized_jpeg(image)
            try:
                upload = await self.client.post(
                    SEARCH_URL,
                    params={
                        "rpt": "imageview",
                        "format": "json",
                        "request": json.dumps(
                            {"blocks": [{"block": "b-page_type_search-by-image__link"}]}
                        ),
                    },
                    files={"upfile": ("photo.jpg", jpeg, "image/jpeg")},
                )
                if upload.status_code in {403, 429} or _challenge(
                    upload.text[:8000], upload.status_code
                ):
                    raise ImageWebSearchBlocked("Yandex upload blocked")
                upload.raise_for_status()
                identifier = _cbir_id(upload.json())
                if not identifier:
                    raise ImageWebSearchUnavailable("Yandex cbirId is unavailable")
                result_url = str(httpx.URL(SEARCH_URL).copy_add_param("cbir_id", identifier))
                all_results: list[ImageWebHit] = []
                for section in ("sites", "similar"):
                    response = await self.client.get(
                        SEARCH_URL,
                        params={"cbir_id": identifier, "rpt": "imageview", "cbir_page": section},
                    )
                    if response.status_code in {403, 429} or _challenge(
                        response.text[:8000], response.status_code
                    ):
                        raise ImageWebSearchBlocked("Yandex results blocked")
                    response.raise_for_status()
                    if len(response.content) > MAX_HTML_BYTES:
                        raise ImageWebSearchUnavailable("Yandex response oversized")
                    all_results.extend(parse_yandex_results(response.text))
                unique: dict[tuple[str, str], ImageWebHit] = {}
                for hit in all_results:
                    unique[(hit.image_url, hit.source_url)] = hit
                results = tuple(unique.values())[:MAX_CARDS]
                data = ImageWebResult(results, result_url)
            except ImageWebSearchBlocked:
                self._blocked_until = monotonic() + BLOCK_SECONDS
                raise
            except (httpx.RequestError, httpx.HTTPStatusError, ValueError, KeyError) as error:
                logger.warning(
                    "Image web provider unavailable | error_type=%s", type(error).__name__
                )
                raise ImageWebSearchUnavailable("Image search service unavailable") from error
            if len(self._cache) >= 128:
                self._cache.clear()
            self._cache[digest] = (monotonic() + self._cache_ttl, data)
            return _ranked(data, preferred_domains)


def image_web_page(
    result: ImageWebResult, page: int, *, size: int = PAGE_SIZE
) -> tuple[ImageWebHit, ...]:
    if size <= 0 or page < 0:
        raise ValueError("Invalid page")
    return result.results[page * size : (page + 1) * size]
