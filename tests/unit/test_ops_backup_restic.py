"""Restic backups are explicit, bounded, and never imply restore success."""

import subprocess
from pathlib import Path

import pytest

from office_assistant.scripts import backup_restic


def test_prerequisites_refuse_missing_password_and_nested_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(backup_restic.shutil, "which", lambda _binary: "/usr/bin/mock")
    env = {
        "RESTIC_REPOSITORY": str(storage / "backups"),
        "RESTIC_PASSWORD_FILE": str(tmp_path / "absent"),
        "PGDATABASE": "synthetic",
    }
    result = backup_restic.prerequisites(storage, env)
    assert "repository_inside_source" in result
    assert "restic_password_file_missing" in result


def test_dry_run_never_invokes_pg_dump_or_restic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    secret = tmp_path / "password"
    secret.write_text("test-only", encoding="utf-8")
    environment = {
        "RESTIC_REPOSITORY": "s3:example/backups",
        "RESTIC_PASSWORD_FILE": str(secret),
        "PGDATABASE": "synthetic",
    }
    monkeypatch.setattr(backup_restic.shutil, "which", lambda _binary: "/usr/bin/mock")
    monkeypatch.setattr(backup_restic.os, "environ", environment)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("no subprocess in dry run")

    monkeypatch.setattr(backup_restic.subprocess, "run", refuse)
    monkeypatch.setattr("sys.argv", ["backup_restic", "--storage-root", str(storage)])
    backup_restic.main()
    assert '"DRY_RUN"' in capsys.readouterr().out


def test_apply_requires_explicit_quiescence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    secret = tmp_path / "password"
    secret.write_text("test-only", encoding="utf-8")
    monkeypatch.setattr(backup_restic.shutil, "which", lambda _binary: "/usr/bin/mock")
    monkeypatch.setattr(
        backup_restic.os,
        "environ",
        {
            "RESTIC_REPOSITORY": "s3:example/backups",
            "RESTIC_PASSWORD_FILE": str(secret),
            "PGDATABASE": "synthetic",
        },
    )
    monkeypatch.setattr(
        "sys.argv", ["backup_restic", "--storage-root", str(storage), "--apply"]
    )
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
