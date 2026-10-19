"""Operator-only Restic snapshot for quiesced PostgreSQL and private storage.

Live storage and a database dump cannot form a consistent snapshot while
writers are active. Never stop services, read .env, delete files or restore to
production from this utility. The operator confirms quiescence separately.
"""

import argparse
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from stat import S_IMODE
from tempfile import TemporaryDirectory


class BackupPrerequisiteError(RuntimeError):
    """A backup cannot safely execute in this environment."""


def prerequisites(
    storage_root: Path, environment: dict[str, str], staging_root: Path | None = None
) -> list[str]:
    """Return only safe, nonsecret labels."""
    problems: list[str] = []
    if not storage_root.is_dir() or storage_root.is_symlink():
        problems.append("storage_root_missing_or_linked")
    if staging_root is None or not staging_root.is_dir() or staging_root.is_symlink():
        problems.append("private_staging_missing")
    else:
        try:
            staging = staging_root.resolve(strict=True)
            source = storage_root.resolve()
            if staging == source or staging in source.parents or source in staging.parents:
                problems.append("staging_overlaps_source")
            attributes = staging.stat()
            if attributes.st_uid != os.getuid() or S_IMODE(attributes.st_mode) & 0o077:
                problems.append("staging_permissions_not_private")
        except OSError:
            problems.append("private_staging_unavailable")
    for key in ("RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE", "PGDATABASE"):
        if not environment.get(key):
            problems.append(f"missing_{key.lower()}")
    password_file = environment.get("RESTIC_PASSWORD_FILE")
    if password_file and not Path(password_file).is_file():
        problems.append("restic_password_file_missing")
    repository = environment.get("RESTIC_REPOSITORY", "")
    if repository.startswith("/"):
        try:
            Path(repository).resolve().relative_to(storage_root.resolve())
        except ValueError:
            pass
        else:
            problems.append("repository_inside_source")
    for binary in ("pg_dump", "restic"):
        if shutil.which(binary) is None:
            problems.append(f"missing_{binary}")
    return problems


def run_command(args: list[str], *, timeout: int, environment: dict[str, str]) -> None:
    """Do not disclose child stderr, which may contain private file paths."""
    try:
        completed = subprocess.run(
            args,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        raise BackupPrerequisiteError(f"backup_command_failed_{type(error).__name__}") from None
    if completed.returncode != 0:
        raise BackupPrerequisiteError(f"backup_command_exit_{completed.returncode}")


def snapshot(
    storage_root: Path, environment: dict[str, str], staging_root: Path
) -> None:
    """Write one Restic snapshot covering the SQL dump and original blobs."""
    storage = storage_root.resolve(strict=True)
    previous_umask = os.umask(0o077)
    try:
        with TemporaryDirectory(prefix="office-backup-", dir=staging_root) as directory:
            staging = Path(directory)
            os.chmod(staging, 0o700)
            dump = staging / "postgres.dump"
            run_command(
                ["pg_dump", "--format=custom", "--no-owner", "--no-acl", f"--file={dump}"],
                timeout=900,
                environment=environment,
            )
            if not dump.is_file() or dump.stat().st_size == 0:
                raise BackupPrerequisiteError("postgres_dump_empty")
            manifest = {
                "schema": 1,
                "created_utc": datetime.now(UTC).isoformat(),
                "contents": ["postgres_custom_dump", "original_file_storage"],
                "quiesced_by_operator": True,
                "qdrant": "derived_index_not_in_snapshot_rebuild_required",
            }
            (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            run_command(
                ["restic", "backup", "--tag", "telegram-office", str(staging), str(storage)],
                timeout=3600,
                environment=environment,
            )
    finally:
        os.umask(previous_umask)


def main() -> None:
    parser = argparse.ArgumentParser(description="Operator-only Restic snapshot")
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-services-paused", action="store_true")
    args = parser.parse_args()
    environment = dict(os.environ)
    problems = prerequisites(args.storage_root, environment, args.staging_root)
    if problems:
        print(json.dumps({"status": "BLOCKED", "checks": problems}))
        raise SystemExit(2)
    if not args.apply:
        print(json.dumps({"status": "DRY_RUN", "note": "no backup was created"}))
        return
    if not args.confirm_services_paused:
        print(json.dumps({"status": "BLOCKED", "checks": ["writers_must_be_paused"]}))
        raise SystemExit(2)
    try:
        snapshot(args.storage_root, environment, args.staging_root)
    except (BackupPrerequisiteError, OSError) as error:
        print(json.dumps({"status": "FAILED", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    print(json.dumps({"status": "SNAPSHOT_WRITTEN", "restore_verified": False}))


if __name__ == "__main__":
    main()
