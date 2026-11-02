"""Read-only Kali manual-UAT gate blocks incompatible or unhealthy staging."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/gate_manual_photo_uat.sh"


def test_gate_is_valid_bash_and_never_modifies_staging() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False, timeout=10
    )
    assert result.returncode == 0, result.stderr
    text = SCRIPT.read_text(encoding="utf-8")
    assert "0023_photo_groups" in text
    assert "check_readiness" in text
    assert "check_three_acceptance_gates" in text
    assert "prepare_visual_catalog" in text
    assert "MANUAL_UAT_GATE=TECHNICAL_PASS" in text
    assert "MANUAL_UAT=HUMAN_ACCEPTANCE_REQUIRED" in text
    for forbidden in (
        "docker compose down",
        "docker volume rm",
        "alembic upgrade",
        "git pull",
        "git reset",
        "--force-recreate",
        "--download-model",
    ):
        assert forbidden not in text


def test_invalid_mode_is_rejected_before_any_external_calls() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--deploy"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 2
    assert "reason=invalid_mode" in result.stderr


def _stage(tmp_path: Path) -> dict[str, str]:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(SCRIPT, scripts / SCRIPT.name)
    (tmp_path / ".env").write_text("TEST_STAGING=1\n", encoding="utf-8")
    (tmp_path / "docker-compose.staging.yml").write_text(
        "name: tgstage_ai_assistand\n", encoding="utf-8"
    )
    binaries = tmp_path / "fakebin"
    binaries.mkdir()
    git = binaries / "git"
    git.write_text(
        """#!/usr/bin/env bash
case "$1" in
  branch) echo main;;
  status) exit 0;;
  ls-files) exit 1;;
  rev-parse) echo 1234567890abcdef;;
  *) exit 7;;
esac
""",
        encoding="utf-8",
    )
    docker = binaries / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
case "$1" in
  inspect)
    case "$*" in
      *com.docker.compose.project*) echo tgstage_ai_assistand;;
      *State.Status*) echo running;;
      *State.Health*) echo healthy;;
      *) exit 7;;
    esac;;
  exec)
    case "$2" in
      tgstage_app) test "${SIM_POLLING:-1}" = 1;;
      tgstage_miniapp) test "${SIM_MINIAPP:-1}" = 1;;
      *) exit 8;;
    esac;;
  compose)
    case "$*" in
      *SELECT*version_num*) echo "${SIM_REVISION:-0023_photo_groups}";;
    esac;;
  *) exit 9;;
esac
""",
        encoding="utf-8",
    )
    git.chmod(0o755)
    docker.chmod(0o755)
    return {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"]}


@pytest.mark.parametrize(
    ("mode", "polling", "miniapp", "revision", "ok", "blocked_reason"),
    [
        ("--full", "1", "1", "0023_photo_groups", True, ""),
        ("--photo-only", "1", "0", "0023_photo_groups", True, ""),
        ("--full", "0", "1", "0023_photo_groups", False, "telegram_polling_disabled"),
        ("--full", "1", "0", "0023_photo_groups", False, "miniapp_https_not_configured"),
        ("--full", "1", "1", "0022_inventory_units", False, "schema_migration_required"),
    ],
)
def test_gate_fails_closed_with_stubbed_docker(
    tmp_path: Path,
    mode: str,
    polling: str,
    miniapp: str,
    revision: str,
    ok: bool,
    blocked_reason: str,
) -> None:
    env = _stage(tmp_path)
    env.update(SIM_POLLING=polling, SIM_MINIAPP=miniapp, SIM_REVISION=revision)
    result = subprocess.run(
        ["bash", str(tmp_path / "scripts" / SCRIPT.name), mode],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=12,
    )
    if ok:
        assert result.returncode == 0, result.stderr
        assert "MANUAL_UAT_GATE=TECHNICAL_PASS" in result.stdout
        assert "MANUAL_UAT=HUMAN_ACCEPTANCE_REQUIRED" in result.stdout
    else:
        assert result.returncode == 2
        assert f"reason={blocked_reason}" in result.stderr
        assert "MANUAL_UAT_GATE=TECHNICAL_PASS" not in result.stdout
