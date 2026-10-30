"""Multi-photo web reverse-search is independently bounded and order preserving."""

import asyncio

import pytest

from office_assistant.integrations.telegram.photo_batch import (
    MAX_BATCH_PHOTOS,
    bounded_web_photo_album,
)
from office_assistant.web_evidence.image_search import ImageWebResult


def test_success_and_provider_error_do_not_merge_results_or_leak_photo_bytes() -> None:
    requested: list[str] = []

    async def download(key: str) -> bytes:
        requested.append(key)
        if key == "second":
            raise OSError("Telegram source inaccessible")
        return key.encode("ascii")

    async def search(body: bytes) -> ImageWebResult:
        return ImageWebResult((), f"https://yandex.com/images/search?cbir_id={body.decode()}")

    async def run() -> None:
        result = await bounded_web_photo_album(
            ["first", "second", "third"],
            download=download,
            search=search,
        )
        assert len(result.results) == 3
        assert requested == ["first", "second", "third"]
        assert not result.results[0].blocked
        assert result.results[1].blocked
        assert not result.results[2].blocked
        assert result.failed == 1
        assert result.deadline_skipped == 0
        assert result.incomplete
        assert "third" in result.results[2].provider_url

    asyncio.run(run())


def test_album_deadline_prevents_long_serial_provider_stall() -> None:
    downloaded: list[str] = []

    async def download(key: str) -> bytes:
        downloaded.append(key)
        return b"private-photo"

    async def search(_: bytes) -> ImageWebResult:
        await asyncio.sleep(0.1)
        return ImageWebResult((), "https://yandex.com/images/search")

    async def run() -> None:
        result = await bounded_web_photo_album(
            ["first", "second", "third"],
            download=download,
            search=search,
            total_seconds=0.01,
            per_photo_seconds=0.03,
        )
        assert len(result.results) == 3
        assert result.failed == 1
        assert result.deadline_skipped == 2
        assert downloaded == ["first"]
        assert all(item.blocked for item in result.results)

    asyncio.run(run())


def test_oversized_album_is_rejected_before_any_network_call() -> None:
    async def download(_: str) -> bytes:
        pytest.fail("No file may be downloaded")

    async def search(_: bytes) -> ImageWebResult:
        pytest.fail("No external query may run")

    async def run() -> None:
        with pytest.raises(ValueError):
            await bounded_web_photo_album(
                ["file"] * (MAX_BATCH_PHOTOS + 1),
                download=download,
                search=search,
            )

    asyncio.run(run())


def test_blocked_provider_response_marks_album_incomplete_without_exception() -> None:
    async def download(key: str) -> bytes:
        return key.encode("ascii")

    async def search(body: bytes) -> ImageWebResult:
        return ImageWebResult(
            (),
            "https://yandex.com/images/search",
            blocked=body == b"blocked",
        )

    async def run() -> None:
        result = await bounded_web_photo_album(
            ["first", "blocked", "third"], download=download, search=search
        )
        assert [item.blocked for item in result.results] == [False, True, False]
        assert result.failed == 1
        assert result.deadline_skipped == 0
        assert result.incomplete

    asyncio.run(run())
