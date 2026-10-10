"""Safe local persistent file storage for Phase 3."""

import json
from pathlib import Path
from secrets import token_hex
from shutil import move
from tempfile import NamedTemporaryFile
from typing import Any

_HEX_DIGITS = frozenset("0123456789abcdef")


class StorageError(RuntimeError):
    """Raised when a storage operation cannot be completed safely."""


class LocalFileStorage:
    """Filesystem storage that never trusts user-provided filenames."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.staged_root = self.root / "staged"
        self.blob_root = self.root / "blobs"
        self.derived_root = self.root / "derived"
        self.image_asset_root = self.root / "image_assets"
        self.staged_root.mkdir(parents=True, exist_ok=True)
        self.blob_root.mkdir(parents=True, exist_ok=True)
        self.derived_root.mkdir(parents=True, exist_ok=True)
        self.image_asset_root.mkdir(parents=True, exist_ok=True)

    def create_staging_path(self, suffix: str = "") -> tuple[Path, str]:
        safe_suffix = suffix.lower() if suffix and suffix.startswith(".") else ""
        name = f"{token_hex(16)}{safe_suffix}"
        path = self.staged_root / name
        return path, f"staged/{name}"

    def path_for_key(self, storage_key: str) -> Path:
        # Storage keys are portable POSIX-style relative paths, not OS-native paths.
        # Windows drive letters and backslashes must also be refused on Linux: a key
        # accepted here must never become a traversal if storage moves between hosts.
        if (
            not storage_key
            or any(character in storage_key for character in ("\\", ":", "\\x00"))
            or any(part in ("", ".", "..") for part in storage_key.split("/"))
        ):
            raise StorageError("Invalid storage key")
        candidate = (self.root / storage_key).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise StorageError("Storage key escapes the configured root") from error
        return candidate

    def exists(self, storage_key: str) -> bool:
        return self.path_for_key(storage_key).is_file()

    def size(self, path: Path) -> int:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as error:
            raise StorageError("Path escapes the configured root") from error
        return resolved.stat().st_size

    def activate(self, staged_key: str) -> str:
        source = self.path_for_key(staged_key)
        if not source.is_file():
            raise StorageError("Staged file does not exist")
        blob_name = f"{token_hex(16)}{source.suffix.lower()}"
        destination = self.blob_root / blob_name
        move(str(source), str(destination))
        return f"blobs/{blob_name}"

    def restore(self, active_key: str, staged_key: str) -> None:
        source = self.path_for_key(active_key)
        destination = self.path_for_key(staged_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            move(str(source), str(destination))

    def delete(self, storage_key: str) -> None:
        path = self.path_for_key(storage_key)
        if path.is_file():
            path.unlink()

    def store_image_asset(self, data: bytes, content_sha256: str, suffix: str) -> str:
        """Write image bytes into their own content-addressed namespace, reusing bytes held there.

        Derived images never share the ``blobs/`` root that holds original uploads (Master Spec
        §23.3), and one digest maps to one physical file however many documents cite it (§23.4).
        """

        if len(content_sha256) != 64 or not set(content_sha256) <= _HEX_DIGITS:
            raise StorageError("Image digest is not a SHA-256 hex digest")
        safe_suffix = suffix.lower() if suffix.startswith(".") and suffix[1:].isalnum() else ""
        directory = self.image_asset_root / content_sha256[:2]
        destination = directory / f"{content_sha256}{safe_suffix}"
        if destination.is_file():
            return destination.relative_to(self.root).as_posix()
        directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("wb", dir=directory, prefix=".part-", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(data)
        try:
            temporary_path.replace(destination)
        except OSError:
            temporary_path.unlink(missing_ok=True)
            raise
        return destination.relative_to(self.root).as_posix()

    def write_derived_json(self, file_id: int, run_id: int, payload: dict[str, Any]) -> str:
        return self._write_derived(
            file_id, run_id, "normalized.json", json.dumps(payload, ensure_ascii=False, indent=2)
        )

    def write_derived_text(self, file_id: int, run_id: int, content: str) -> str:
        return self._write_derived(file_id, run_id, "text.txt", content)

    def _write_derived(self, file_id: int, run_id: int, name: str, content: str) -> str:
        directory = self.derived_root / str(file_id) / str(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / name
        with NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        temporary_path.replace(destination)
        return destination.relative_to(self.root).as_posix()
