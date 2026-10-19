"""Restic backups are explicit, bounded, and never imply restore success."""

import subprocess
from pathlib import Path

import pytest

from office_assistant.scripts import backup_restic


def mock_binary(_binary: str) -> str:
    return "/usr/bin/mock"


def test_prerequisites_refuse_missing_password_and_nested_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    staging = tmp_path / "private-staging"
    staging.mkdir(mode=0o700)
    monkeypatch.setattr(backup_restic.shutil, "which", mock_binary)
    env = {
        "RESTIC_REPOSITORY": str(storage / "backups"),
        "RESTIC_PASSWORD_FILE": str(tmp_path / "absent"),
        "PGDATABASE": "synthetic",
    }
    result = backup_restic.prerequisites(storage, env, staging)
    assert "repository_inside_source" in result
    assert "restic_password_file_missing" in result


def test_dry_run_never_invokes_pg_dump_or_restic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    staging = tmp_path / "private-staging"
    staging.mkdir(mode=0o700)
    secret = tmp_path / "password"
    secret.write_text("test-only", encoding="utf-8")
    environment = {
        "RESTIC_REPOSITORY": "s3:example/backups",
        "RESTIC_PASSWORD_FILE": str(secret),
        "PGDATABASE": "synthetic",
    }
    monkeypatch.setattr(backup_restic.shutil, "which", mock_binary)
    monkeypatch.setattr(backup_restic.os, "environ", environment)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("no subprocess in dry run")

    monkeypatch.setattr(backup_restic.subprocess, "run", refuse)
    monkeypatch.setattr("sys.argv", ["backup_restic", "--storage-root", str(storage), "--staging-root", str(staging)])
    backup_restic.main()
    assert '"DRY_RUN"' in capsys.readouterr().out


def test_apply_requires_explicit_quiescence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    staging = tmp_path / "private-staging"
    staging.mkdir(mode=0o700)
    secret = tmp_path / "password"
    secret.write_text("test-only", encoding="utf-8")
    monkeypatch.setattr(backup_restic.shutil, "which", mock_binary)
    monkeypatch.setattr(
        backup_restic.os,
        "environ",
        {
            "RESTIC_REPOSITORY": "s3:example/backups",
            "RESTIC_PASSWORD_FILE": str(secret),
            "PGDATABASE": "synthetic",
        },
    )
    monkeypatch.setattr("sys.argv", [
        "backup_restic", "--storage-root", str(storage), "--staging-root", str(staging), "--apply"
    ])
    with pytest.raises(SystemExit) as raised:
        backup_restic.main()
    assert raised.value.code == 2


def test_failed_child_command_never_exposes_private_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(["restic"], 12, b"", b"customer-secret")

    monkeypatch.setattr(backup_restic.subprocess, "run", fail)
    with pytest.raises(backup_restic.BackupPrerequisiteError) as raised:
        backup_restic.run_command(["restic", "backup"], timeout=1, environment={})
    assert "customer-secret" not in str(raised.value)
    assert "12" in str(raised.value)


def test_staging_requires_private_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o755)
    secret = tmp_path / "secret"
    secret.write_text("test-only", encoding="utf-8")
    monkeypatch.setattr(backup_restic.shutil, "which", mock_binary)
    env = {
        "RESTIC_REPOSITORY": "s3:example/backups",
        "RESTIC_PASSWORD_FILE": str(secret),
        "PGDATABASE": "synthetic",
    }
    assert "staging_permissions_not_private" in backup_restic.prerequisites(storage, env, staging)
    staging.chmod(0o700)
    assert backup_restic.prerequisites(storage, env, staging) == []
    assert "staging_overlaps_source" in backup_restic.prerequisites(storage, env, storage)
