"""Bounded photo albums and internet result paging never create product claims."""

from office_assistant.integrations.telegram.photo_flow import (
    MAX_ALBUM_PHOTOS,
    PhotoFlow,
    add_photo,
)
from office_assistant.web_evidence.image_search import ImageWebHit, ImageWebResult, image_web_page


def test_media_album_is_one_actor_bound_group_with_separate_photos() -> None:
    flow = add_photo(None, 123, "first", 100, 1000, "album-7")
    assert isinstance(flow, PhotoFlow)
    first_token = flow.token
    for i in range(101, 115):
        flow = add_photo(flow, 123, f"file-{i}", i, i + 900, "album-7")
    assert len(flow.photos) == MAX_ALBUM_PHOTOS
    assert flow.token == first_token
    assert flow.valid(123, first_token)
    assert not flow.valid(999, first_token)
    assert not flow.valid(123, "stale-token")
    assert len({item.message_id for item in flow.photos}) == MAX_ALBUM_PHOTOS


def test_unrelated_photo_never_merges_albums() -> None:
    old = add_photo(None, 123, "old", 11, 11, "album-a")
    other = add_photo(old, 123, "new", 12, 12, "album-b")
    assert other.token != old.token
    assert len(other.photos) == 1
    single = add_photo(other, 123, "single", 13, 13, None)
    assert single.token != other.token
    next_single = add_photo(single, 123, "second", 14, 14, None)
    assert len(next_single.photos) == 1


def test_web_pagination_is_bounded_and_never_confirms_sku() -> None:
    hits = tuple(
        ImageWebHit(
            f"Source {i}",
            f"https://img.example/{i}.jpg",
            f"https://pages.example/{i}",
            "",
            "pages.example",
        )
        for i in range(13)
    )
    result = ImageWebResult(hits, "https://yandex.com/images/search")
    assert len(image_web_page(result, 0)) == 5
    assert len(image_web_page(result, 1)) == 5
    assert len(image_web_page(result, 2)) == 3
    assert image_web_page(result, 3) == ()
