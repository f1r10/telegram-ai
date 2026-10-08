from hashlib import sha256
from pathlib import Path

import pytest

from office_assistant.infrastructure.storage.hashing import sha256_file
from office_assistant.infrastructure.storage.local import LocalFileStorage, StorageError


def test_sha256_is_stable_and_distinguishes_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"same content")
    second.write_bytes(b"different content")

    first_hash, first_size = sha256_file(first)
    same_hash, same_size = sha256_file(first)
    second_hash, _ = sha256_file(second)

    assert first_hash == same_hash
    assert first_size == same_size == len(b"same content")
    assert first_hash != second_hash


def test_storage_paths_are_traversal_safe(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "storage")

    for hostile_key in (
        "../secret.txt",
        "..\\..\\secret.txt",
        "C:/Windows/file.txt",
        "/etc/passwd",
    ):
        with pytest.raises(StorageError):
            storage.path_for_key(hostile_key)

    path, key = storage.create_staging_path(".pdf")
    assert path.parent == storage.staged_root
    assert key.startswith("staged/")


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def test_image_asset_bytes_live_in_their_own_content_addressed_namespace(
    tmp_path: Path,
) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    blob = storage.blob_root / "token.jpg"
    blob.write_bytes(b"jpeg bytes")
    digest = _digest(b"jpeg bytes")

    key = storage.store_image_asset(blob, digest, ".jpg")

    assert key == f"image_assets/{digest[:2]}/{digest}.jpg"
    assert storage.path_for_key(key).read_bytes() == b"jpeg bytes"
    assert blob.is_file(), "the original upload must survive registration"


def test_identical_image_bytes_are_stored_once_however_many_sources_show_them(
    tmp_path: Path,
) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    digest = _digest(b"shared logo")
    first = storage.staged_root / "a.png"
    second = storage.staged_root / "b.png"
    for source in (first, second):
        source.write_bytes(b"shared logo")

    first_key = storage.store_image_asset(first, digest, ".png")
    second_key = storage.store_image_asset(second, digest, ".png")

    assert first_key == second_key
    assert [path.name for path in storage.image_asset_root.rglob("*") if path.is_file()] == [
        f"{digest}.png"
    ]


def test_different_image_bytes_get_separate_assets(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    left = storage.staged_root / "left.png"
    right = storage.staged_root / "right.png"
    left.write_bytes(b"first")
    right.write_bytes(b"second")

    left_key = storage.store_image_asset(left, _digest(b"first"), ".png")
    right_key = storage.store_image_asset(right, _digest(b"second"), ".png")

    assert left_key != right_key


def test_image_asset_storage_refers_bytes_and_keys_outside_the_root(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "storage")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"not ours")

    with pytest.raises(StorageError):
        storage.store_image_asset(outside, _digest(b"not ours"), ".jpg")
    with pytest.raises(StorageError):
        storage.store_image_asset(storage.staged_root / "x", "not-a-digest", ".jpg")
