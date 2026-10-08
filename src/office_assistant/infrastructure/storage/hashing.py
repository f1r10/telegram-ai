"""Incremental SHA-256 hashing."""

import hashlib
from pathlib import Path


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    """Hash a file incrementally and return its hex digest and byte size."""

    digest = hashlib.sha256()
    byte_size = 0
    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)
            byte_size += len(chunk)
    return digest.hexdigest(), byte_size
