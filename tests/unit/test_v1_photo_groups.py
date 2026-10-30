# pyright: reportPrivateUsage=false
"""Group metadata and pending-append state never authorize unrelated users."""

from time import monotonic

import pytest

from office_assistant.application.photo_groups import PhotoGroupView, validated_description
from office_assistant.integrations.telegram.handlers import (
    PHOTO_GROUP_APPEND_KEY,
    _photo_group_append_id,
)


def test_group_metadata_validation_preserves_explicit_title() -> None:
    assert validated_description(" Ad: Qara saat\nTəsvir: Metal ") == (
        "Ad: Qara saat\nTəsvir: Metal"
    )
    assert PhotoGroupView(5, "Ad: Qara saat", 2, 3).title == "Qara saat"
    with pytest.raises(ValueError):
        validated_description("Bu, yalnız sərbəst yazıdır")
    with pytest.raises(ValueError):
        validated_description("Ad: " + "x" * 3100)


def test_group_append_token_belongs_to_actor_and_expires() -> None:
    data: dict[str, object] = {
        PHOTO_GROUP_APPEND_KEY: (123, 9, monotonic() + 120)
    }
    assert _photo_group_append_id(data, 123) == 9
    assert _photo_group_append_id(data, 456) is None
    assert PHOTO_GROUP_APPEND_KEY not in data
    data[PHOTO_GROUP_APPEND_KEY] = (123, 10, monotonic() - 1)
    assert _photo_group_append_id(data, 123) is None
    assert PHOTO_GROUP_APPEND_KEY not in data
