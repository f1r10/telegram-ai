"""Owner Colab cbirId provider: no hidden product claims, no CAPTCHA bypass."""

import asyncio
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from office_assistant.web_evidence.image_search import (
    ImageWebSearchBlocked,
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
    return '<div data-state=\'' + json.dumps(payload) + "\'></div>"


def test_parse_preserves_only_real_same_card_image_page_pairs() -> None:
    hits = parse_yandex_results(html(), ("example.az",))
    assert len(hits) == 2
    assert hits[0].priority and hits[0].domain == "shop.example.az"
    assert hits[0].source_url == "https://shop.example.az/p/3"
    assert hits[1].source_url == "https://ordinary.com/desk"
    assert all(hit.source_url.startswith("https://") for hit in hits)


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
            with pytest.raises(Exception):
                await service.search(picture())

    asyncio.run(scenario())
