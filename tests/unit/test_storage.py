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
