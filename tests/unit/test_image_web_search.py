"""Owner Colab cbirId provider: no hidden product claims, no CAPTCHA bypass."""

import asyncio
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from office_assistant.web_evidence.image_search import (
    ImageWebSearchBlocked,
    ImageWebSearchUnavailable,
    YandexReverseImageSearch,
    image_web_page,
    parse_yandex_results,
)


def picture() -> bytes:
    file = BytesIO()
    Image.new("RGB", (120, 100), "red").save(file, format="PNG")
    return file.getvalue()


def html() -> str:
    payload = {
        "initialState": {
            "cbirSites": {
                "sites": [
                    {
                        "title": "Regular image",
                        "url": "https://ordinary.com/desk",
                        "originalImage": {"url": "https://img.other.com/desk.jpg"},
                        "thumb": {"url": "https://img.other.com/preview.jpg"},
                    },
                    {
                        "title": "Preferred site",
                        "url": "https://shop.example.az/p/3",
                        "originalImage": {"url": "https://cdn.example.az/3.jpg"},
                    },
                    {"url": "javascript:alert(1)", "originalImage": {"url": "https://img/p.jpg"}},
                    {"url": "https://shop.example.az/4", "originalImage": {"url": ""}},
                ]
            }
        }
    }
    return "<div data-state='" + json.dumps(payload) + "'></div>"


def test_parse_preserves_only_real_same_card_image_page_pairs() -> None:
    hits = parse_yandex_results(html(), ("example.az",))
    assert len(hits) == 2
    assert hits[0].priority and hits[0].domain == "shop.example.az"
    assert hits[0].source_url == "https://shop.example.az/p/3"
    assert hits[1].source_url == "https://ordinary.com/desk"
    assert all(hit.source_url.startswith("https://") for hit in hits)


def test_image_search_never_presents_internal_or_malformed_provider_links() -> None:
    private_pages = [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/home",
        "https://router.local/secret",
        "https://intranet.internal/stock",
        "https://shop.example.az:99999/item",
    ]
    cases = [
        {
            "title": f"Untrusted {index}",
            "url": page,
            "originalImage": {"url": "https://cdn.example.az/photo.jpg"},
        }
        for index, page in enumerate(private_pages)
    ]
    cases.append(
        {
            "title": "Public source, bad thumbnail",
            "url": "https://shop.example.az/item",
            "originalImage": {"url": "https://127.0.0.1/private.jpg"},
        }
    )
    cases.append(
        {
            "title": "Safe",
            "url": "https://shop.example.az/safe",
            "originalImage": {"url": "https://cdn.example.az/safe.jpg"},
        }
    )
    state = {"initialState": {"cbirSites": {"sites": cases}}}
    markup = "<div data-state='" + json.dumps(state) + "'></div>"
    matches = parse_yandex_results(markup)
    assert len(matches) == 1
    assert matches[0].source_url == "https://shop.example.az/safe"
    assert matches[0].image_url == "https://cdn.example.az/safe.jpg"


def test_browser_ip_shortcuts_and_wildcard_loopback_names_are_never_presented() -> None:
    risky = [
        "http://127.1/secret",
        "http://0177.0.0.1/internal",
        "http://2130706433/admin",
        "http://0x7f000001/meta",
        "https://127.0.0.1.nip.io/test",
        "https://127-0-0-1.sslip.io/private",
        "https://localhost.localtest.me/private",
        "https://router.lan/index",
        "https://scanner.home/private",
        "https://169.254.169.254.nip.io/metadata",
        "https://public.example:65536/product",
        "http://127%2e0%2e0%2e1/config",
        "http://intranet/resource",
    ]
    entries = [
        {
            "title": f"Rejected {number}",
            "url": url,
            "originalImage": {"url": "https://cdn.example.az/safe.jpg"},
        }
        for number, url in enumerate(risky)
    ]
    entries.append(
        {
            "title": "Valid product",
            "url": "https://shop.example.az/watch?id=12",
            "originalImage": {"url": "https://cdn.example.az/watch.jpg"},
        }
    )
    state = {"initialState": {"cbirSites": {"sites": entries}}}
    html = "<div data-state='" + json.dumps(state) + "'></div>"
    found = parse_yandex_results(html)
    assert len(found) == 1
    assert found[0].source_url == "https://shop.example.az/watch?id=12"
    assert found[0].image_url == "https://cdn.example.az/watch.jpg"


def test_html_card_pair_never_joins_sibling_source_links() -> None:
    html = (
        "<ul>"
        "<li><a href='https://site.az/item-22'>source</a>"
        "<a href='https://cdn.az/22.jpg'><img alt='watch'></a></li>"
        "<li><a href='https://site.az/item-23'>other page</a></li>"
        "<li><a href='https://cdn.az/unpaired.jpg'>lonely photo</a></li>"
        "</ul>"
    )
    found = parse_yandex_results(html)
    assert len(found) == 1
    assert found[0].image_url == "https://cdn.az/22.jpg"
    assert found[0].source_url == "https://site.az/item-22"


def test_consent_only_search_cache_and_priority_ranking() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"blocks": [{"params": {"cbirId": "token001"}}]})
        return httpx.Response(200, text=html())

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = YandexReverseImageSearch(client=client)
            a = await service.search(picture(), preferred_domains=("example.az",))
            b = await service.search(picture(), preferred_domains=("ordinary.com",))
            assert not a.cache_hit and b.cache_hit
            assert a.results[0].domain == "shop.example.az"
            assert b.results[0].domain == "ordinary.com"
            assert len(requests) == 3
            assert len(image_web_page(a, 0, size=1)) == 1
            assert image_web_page(a, 2, size=1) == ()
            assert "cbir_id=token001" in a.provider_url

    asyncio.run(scenario())


def test_challenge_circuit_breaker_does_not_retry_or_bypass() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="<title>Are you not a robot</title>")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = YandexReverseImageSearch(client=client)
            with pytest.raises(ImageWebSearchBlocked):
                await service.search(picture())
            with pytest.raises(ImageWebSearchBlocked):
                await service.search(picture())

    asyncio.run(scenario())
    assert calls == 1


def test_wrong_content_never_becomes_successful_search() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>layout changed</html>")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = YandexReverseImageSearch(client=client)
            with pytest.raises(ImageWebSearchUnavailable):
                await service.search(picture())

    asyncio.run(scenario())
