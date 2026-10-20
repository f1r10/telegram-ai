"""Non-destructive Restic restore drill: verify artifacts in an isolated temp tree.

This utility NEVER invokes pg_restore against any database. Only a full,
explicitly named Restic snapshot is restored into a newly created PRIVATE
temporary directory, its manifest is checked and pg_restore --list confirms
that a nonempty custom-format PostgreSQL dump can be read. This does NOT
establish transactional production recovery, Qdrant rebuilding or RPO/RTO.
"""

import argparse
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from stat import S_IMODE
from tempfile import TemporaryDirectory
from typing import cast

from office_assistant.scripts.backup_restic import BackupPrerequisiteError, run_command

_SNAPSHOT_ID = re.compile(r"[0-9a-f]{64}\Z")
_MAX_MANIFEST_BYTES = 16 * 1024
_MAX_ENTRIES = 200_000


def _overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _local_repository(value: str) -> Path | None:
    if value.startswith("local:"):
        return Path(value[6:]).expanduser().resolve()
    if ":" not in value:
        return Path(value).expanduser().resolve()
    return None


def preflight(
    storage_root: Path,
    staging_root: Path,
    environment: dict[str, str],
    snapshot_id: str,
) -> list[str]:
    """Only safe machine-readable reason codes; no file names or secrets."""
    issues: list[str] = []
    if _SNAPSHOT_ID.fullmatch(snapshot_id) is None:
        issues.append("snapshot_full_id_required")
    if not storage_root.is_dir() or storage_root.is_symlink() or not storage_root.is_absolute():
        issues.append("source_storage_invalid")
    if not staging_root.is_dir() or staging_root.is_symlink() or not staging_root.is_absolute():
        issues.append("private_staging_invalid")
    else:
        try:
            attrs = staging_root.stat()
            if attrs.st_uid != os.getuid() or S_IMODE(attrs.st_mode) & 0o077:
                issues.append("private_staging_permissions")
        except OSError:
            issues.append("private_staging_unavailable")
    if not issues or not any(
        issue in issues for issue in ("source_storage_invalid", "private_staging_invalid")
    ):
        try:
            source = storage_root.resolve(strict=True)
            staging = staging_root.resolve(strict=True)
            if source == Path("/") or _overlap(source, staging):
                issues.append("restore_target_overlaps_source")
            repository = _local_repository(environment.get("RESTIC_REPOSITORY", ""))
            if repository is not None and (
                _overlap(repository, staging) or _overlap(repository, source)
            ):
                issues.append("restic_repository_overlaps_storage")
        except (OSError, ValueError):
            issues.append("paths_unavailable")
    for variable in ("RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE"):
        if not environment.get(variable):
            issues.append("missing_" + variable.lower())
    password = environment.get("RESTIC_PASSWORD_FILE", "")
    if password:
        try:
            secret = Path(password)
            if (
                not secret.is_file()
                or secret.is_symlink()
                or secret.stat().st_uid != os.getuid()
                or S_IMODE(secret.stat().st_mode) & 0o077
            ):
                issues.append("password_file_not_private")
        except OSError:
            issues.append("password_file_unavailable")
    for binary in ("restic", "pg_restore"):
        if shutil.which(binary) is None:
            issues.append("missing_" + binary)
    return issues


def _safe_restored_path(target: Path, original: Path) -> Path:
    """Map one absolute source path below the Restic --target tree."""
    if not original.is_absolute() or original == Path("/"):
        raise BackupPrerequisiteError("invalid_original_storage_path")
    current = target
    for item in original.parts[1:]:
        if item in ("", ".", ".."):
            raise BackupPrerequisiteError("invalid_original_storage_path")
        current = current / item
        if current.is_symlink():
            raise BackupPrerequisiteError("restored_path_is_symlink")
    if not current.is_dir():
        raise BackupPrerequisiteError("restored_storage_missing")
    return current


def _manifest_parent(target: Path) -> Path:
    """Find exactly one valid manifest/dump pair without following symlinks."""
    matches: list[Path] = []
    scanned = 0
    for dirname, directories, files in os.walk(target, followlinks=False):
        directories[:] = [name for name in directories if not (Path(dirname) / name).is_symlink()]
        scanned += len(directories) + len(files)
        if scanned > _MAX_ENTRIES:
            raise BackupPrerequisiteError("restored_tree_scan_limit")
        if "manifest.json" in files:
            candidate = Path(dirname) / "manifest.json"
            if candidate.is_symlink() or not candidate.is_file():
                raise BackupPrerequisiteError("manifest_unsafe")
            try:
                if candidate.stat().st_size > _MAX_MANIFEST_BYTES:
                    raise BackupPrerequisiteError("manifest_size_invalid")
                data: object = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError):
                raise BackupPrerequisiteError("manifest_invalid") from None
            if not isinstance(data, dict):
                raise BackupPrerequisiteError("manifest_invalid")
            payload = cast(dict[str, object], data)
            if (
                payload.get("schema") == 1
                and payload.get("quiesced_by_operator") is True
                and payload.get("contents")
                == ["postgres_custom_dump", "original_file_storage"]
                and payload.get("qdrant") == "derived_index_not_in_snapshot_rebuild_required"
                and isinstance(payload.get("created_utc"), str)
            ):
                try:
                    datetime.fromisoformat(cast(str, payload["created_utc"]))
                except ValueError:
                    raise BackupPrerequisiteError("manifest_timestamp_invalid") from None
                matches.append(candidate.parent)
    if len(matches) != 1:
        raise BackupPrerequisiteError("unique_valid_manifest_required")
    return matches[0]


def inspect_restored(
    target: Path,
    source_storage_root: Path,
    environment: dict[str, str],
) -> None:
    """Artifact-level verification only; NEVER apply a PostgreSQL dump."""
    _safe_restored_path(target, source_storage_root)
    dump = _manifest_parent(target) / "postgres.dump"
    if dump.is_symlink() or not dump.is_file() or not 5 <= dump.stat().st_size <= 128 * 1024**3:
        raise BackupPrerequisiteError("postgres_dump_missing_or_invalid")
    with dump.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise BackupPrerequisiteError("postgres_custom_dump_magic_invalid")
    # --list only parses the archive TOC, without connecting to a database.
    run_command(["pg_restore", "--list", str(dump)], timeout=300, environment=environment)


def verify(
    storage_root: Path,
    staging_root: Path,
    environment: dict[str, str],
    *,
    snapshot_id: str,
) -> None:
    failures = preflight(storage_root, staging_root, environment, snapshot_id)
    if failures:
        raise BackupPrerequisiteError("verification_preflight_failed")
    old_umask = os.umask(0o077)
    try:
        with TemporaryDirectory(prefix="office-restore-drill-", dir=staging_root) as folder:
            target = Path(folder)
            os.chmod(target, 0o700)
            run_command(
                ["restic", "restore", snapshot_id, "--target", str(target)],
                timeout=3600,
                environment=environment,
            )
            inspect_restored(target, storage_root, environment)
    finally:
        os.umask(old_umask)


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated, read-only Restic artifact drill")
    parser.add_argument("--storage-root", required=True, type=Path)
    parser.add_argument("--staging-root", required=True, type=Path)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-isolated", action="store_true")
    args = parser.parse_args()
    env = dict(os.environ)
    issues = preflight(args.storage_root, args.staging_root, env, args.snapshot_id)
    if issues:
        print(json.dumps({"status": "BLOCKED", "checks": issues}))
        raise SystemExit(2)
    if not args.apply:
        print(json.dumps({"status": "DRY_RUN", "artifacts_verified": False}))
        return
    if not args.confirm_isolated:
        print(json.dumps({"status": "BLOCKED", "checks": ["isolated_drill_confirmation_required"]}))
        raise SystemExit(2)
    try:
        verify(args.storage_root, args.staging_root, env, snapshot_id=args.snapshot_id)
    except (BackupPrerequisiteError, OSError, ValueError) as error:
        print(json.dumps({"status": "FAILED", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    print(json.dumps({
        "status": "SNAPSHOT_ARTIFACTS_VERIFIED",
        "postgres_dump_toc_readable": True,
        "database_restore_verified": False,
        "qdrant_rebuild_verified": False,
        "production_recovery_approved": False,
    }))


if __name__ == "__main__":
    main()
