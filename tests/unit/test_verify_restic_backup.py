"""Restic artifact restore verification is isolated, explicit and non-destructive."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from office_assistant.scripts import verify_restic_backup

SNAPSHOT = "a" * 64


def private_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, dict[str, str]]:
    storage = tmp_path / "storage"
    staging = tmp_path / "staging"
    storage.mkdir(mode=0o700)
    staging.mkdir(mode=0o700)
    staging.chmod(0o700)
    secret = tmp_path / "password"
    secret.write_text("synthetic", encoding="utf-8")
    secret.chmod(0o600)
    env = {
        "RESTIC_REPOSITORY": "s3:fake-not-accessed/repository",
        "RESTIC_PASSWORD_FILE": str(secret),
    }
    monkeypatch.setattr(verify_restic_backup.shutil, "which", lambda _name: "/mock/bin")
    return storage, staging, env


def test_restore_preflight_fails_closed_for_paths_secrets_and_snapshot_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, staging, env = private_layout(tmp_path, monkeypatch)
    assert verify_restic_backup.preflight(storage, staging, env, SNAPSHOT) == []
    assert "snapshot_full_id_required" in verify_restic_backup.preflight(
        storage, staging, env, "latest"
    )
    assert "restore_target_overlaps_source" in verify_restic_backup.preflight(
        storage, storage, env, SNAPSHOT
    )
    assert "restic_repository_overlaps_storage" in verify_restic_backup.preflight(
        storage, staging, env | {"RESTIC_REPOSITORY": str(staging / "restic")}, SNAPSHOT
    )
    secret = Path(env["RESTIC_PASSWORD_FILE"])
    secret.chmod(0o644)
    assert "password_file_not_private" in verify_restic_backup.preflight(
        storage, staging, env, SNAPSHOT
    )
    secret.chmod(0o600)
    staging.chmod(0o755)
    assert "private_staging_permissions" in verify_restic_backup.preflight(
        storage, staging, env, SNAPSHOT
    )


def seed_restored(target: Path, storage: Path, *, good: bool = True) -> None:
    source = target.joinpath(*storage.parts[1:])
    source.mkdir(parents=True, exist_ok=True)
    (source / "known-fixture.txt").write_text("synthetic-only", encoding="utf-8")
    archive = target / "staged" / "operator-private"
    archive.mkdir(parents=True)
    (archive / "manifest.json").write_text(
        json.dumps({
            "schema": 1,
            "created_utc": datetime.now(UTC).isoformat(),
            "contents": ["postgres_custom_dump", "original_file_storage"],
            "quiesced_by_operator": True,
            "qdrant": "derived_index_not_in_snapshot_rebuild_required",
        }),
        encoding="utf-8",
    )
    (archive / "postgres.dump").write_bytes(b"PGDMP" if good else b"NOT_A_DUMP")


def test_restore_artifacts_are_inspected_without_pg_database_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, staging, env = private_layout(tmp_path, monkeypatch)
    commands: list[list[str]] = []

    def fake_command(
        args: list[str], *, timeout: int, environment: dict[str, str]
    ) -> None:
        assert environment == env
        commands.append(args)
        if args[0] == "restic":
            assert args[:3] == ["restic", "restore", SNAPSHOT]
            assert args[3] == "--target"
            destination = Path(args[4])
            assert destination.is_dir()
            assert destination.parent == staging
            seed_restored(destination, storage)
        elif args[0] == "pg_restore":
            assert args[:2] == ["pg_restore", "--list"]
            assert Path(args[2]).read_bytes().startswith(b"PGDMP")
        else:
            raise AssertionError("Never invoke a database write command")

    monkeypatch.setattr(verify_restic_backup, "run_command", fake_command)
    verify_restic_backup.verify(storage, staging, env, snapshot_id=SNAPSHOT)
    assert [cmd[0] for cmd in commands] == ["restic", "pg_restore"]
    assert not list(staging.iterdir())


def test_corrupt_dump_is_rejected_and_temp_files_are_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, staging, env = private_layout(tmp_path, monkeypatch)
    calls: list[str] = []

    def fake_command(
        args: list[str], *, timeout: int, environment: dict[str, str]
    ) -> None:
        calls.append(args[0])
        if args[0] == "restic":
            seed_restored(Path(args[4]), storage, good=False)

    monkeypatch.setattr(verify_restic_backup, "run_command", fake_command)
    with pytest.raises(verify_restic_backup.BackupPrerequisiteError, match="magic_invalid"):
        verify_restic_backup.verify(storage, staging, env, snapshot_id=SNAPSHOT)
    assert calls == ["restic"]
    assert list(staging.iterdir()) == []


def test_dry_run_never_executes_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    storage, staging, env = private_layout(tmp_path, monkeypatch)
    monkeypatch.setattr(verify_restic_backup.os, "environ", env)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("No external commands on dry-run")

    monkeypatch.setattr(verify_restic_backup, "run_command", refuse)
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_restic_backup",
            "--storage-root", str(storage),
            "--staging-root", str(staging),
            "--snapshot-id", SNAPSHOT,
        ],
    )
    verify_restic_backup.main()
    message = json.loads(capsys.readouterr().out)
    assert message["status"] == "DRY_RUN"
    assert message["artifacts_verified"] is False


def test_restored_symlink_source_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "storage"
    target = tmp_path / "target"
    target.mkdir()
    # No restored file may escape through a file-storage path symlink.
    mapped = target.joinpath(*source.parts[1:])
    mapped.parent.mkdir(parents=True)
    mapped.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(verify_restic_backup.BackupPrerequisiteError, match="symlink"):
        verify_restic_backup.inspect_restored(target, source, {})
