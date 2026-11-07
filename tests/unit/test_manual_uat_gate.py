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
    assert "MANUAL_UAT_CHECK=" in text
    assert "timeout --signal=TERM --kill-after=5s" in text
    assert "docker exec tgstage_postgres sh -c" in text
    assert "docker exec tgstage_app /app/.venv/bin/python -m office_assistant.scripts.check_readiness" in text
    assert "docker exec tgstage_app /app/.venv/bin/python -m office_assistant.scripts.check_three_acceptance_gates" in text
    assert "docker exec tgstage_app /app/.venv/bin/python -m office_assistant.scripts.prepare_visual_catalog" in text
    assert "compose_timed 65 exec -T app" not in text
    assert 'PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -X -w -h 127.0.0.1 -p 5432' in text
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
      tgstage_app)
        case "$*" in
          *PhotoGroupService*) test "${SIM_PHOTO_APP:-1}" = 1;;
          *check_readiness*) exit "${SIM_READINESS_EXIT:-0}";;
          *check_three_acceptance_gates*) exit "${SIM_PREFLIGHT_EXIT:-0}";;
          *prepare_visual_catalog*) exit "${SIM_CATALOG_EXIT:-0}";;
          *) test "${SIM_POLLING:-1}" = 1;;
        esac;;
      tgstage_worker) test "${SIM_PHOTO_WORKER:-1}" = 1;;
      tgstage_miniapp) test "${SIM_MINIAPP:-1}" = 1;;
      tgstage_postgres)
        case "$*" in
          *"SELECT 1"*) test "${SIM_SQL_FAIL:-0}" = 0 && echo 1;;
          *to_regclass*) echo "${SIM_TABLE:-alembic_version}";;
          *"SELECT version_num"*)
            test "${SIM_REVISION_QUERY_FAIL:-0}" = 0 || exit 13
            echo "${SIM_REVISION:-0023_photo_groups}";;
          *) exit 14;;
        esac;;
      *) exit 8;;
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
        ("--full", "0", "1", "0023_photo_groups", False, "telegram_polling_failed"),
        ("--full", "1", "0", "0023_photo_groups", False, "miniapp_https_configuration_failed"),
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
        assert "MANUAL_UAT_CHECK=postgres_sql status=PASS" in result.stdout
        assert "MANUAL_UAT_CHECK=schema_revision status=PASS" in result.stdout
        assert "MANUAL_UAT_CHECK=live_readiness status=PASS" in result.stdout
        assert "MANUAL_UAT_CHECK=visual_catalog_dry_run status=PASS" in result.stdout
    else:
        assert result.returncode == 2
        assert f"reason={blocked_reason}" in result.stderr
        assert "MANUAL_UAT_GATE=TECHNICAL_PASS" not in result.stdout


@pytest.mark.parametrize(
    ("environment", "blocked_reason"),
    [
        ({"SIM_PHOTO_APP": "0"}, "app_photo_group_runtime_failed"),
        ({"SIM_PHOTO_WORKER": "0"}, "worker_photo_group_runtime_failed"),
    ],
)
def test_gate_checks_photo_group_runtime_in_both_containers(
    tmp_path: Path, environment: dict[str, str], blocked_reason: str
) -> None:
    env = _stage(tmp_path)
    env.update(environment)
    result = subprocess.run(
        ["bash", str(tmp_path / "scripts" / SCRIPT.name), "--photo-only"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=12,
    )
    assert result.returncode == 2
    assert "MANUAL_UAT_CHECK=schema_revision status=PASS" in result.stdout
    assert f"reason={blocked_reason}" in result.stderr
    assert "MANUAL_UAT_GATE=TECHNICAL_PASS" not in result.stdout


@pytest.mark.parametrize(
    ("exit_code", "blocked_reason"),
    [
        ("1", "live_readiness_failed"),
        ("124", "live_readiness_timeout"),
    ],
)
def test_gate_labels_slow_or_failed_readiness_without_leaking_output(
    tmp_path: Path, exit_code: str, blocked_reason: str
) -> None:
    env = _stage(tmp_path)
    env["SIM_READINESS_EXIT"] = exit_code
    result = subprocess.run(
        ["bash", str(tmp_path / "scripts" / SCRIPT.name), "--photo-only"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=12,
    )
    assert result.returncode == 2
    assert "MANUAL_UAT_CHECK=schema_revision status=PASS" in result.stdout
    assert "MANUAL_UAT_CHECK=live_readiness status=RUNNING" in result.stdout
    assert "MANUAL_UAT_CHECK=live_readiness status=PASS" not in result.stdout
    assert f"reason={blocked_reason}" in result.stderr
    assert "MANUAL_UAT_GATE=TECHNICAL_PASS" not in result.stdout


@pytest.mark.parametrize(
    ("environment", "expected_reason"),
    [
        ({"SIM_SQL_FAIL": "1"}, "postgres_sql_connection_failed"),
        ({"SIM_TABLE": "MISSING"}, "schema_version_table_missing"),
        ({"SIM_REVISION_QUERY_FAIL": "1"}, "schema_revision_query_failed"),
        ({"SIM_REVISION": "0022_inventory_units"}, "schema_migration_required"),
    ],
)
def test_schema_probe_classifies_read_only_failure_without_raw_database_errors(
    tmp_path: Path, environment: dict[str, str], expected_reason: str
) -> None:
    env = _stage(tmp_path)
    env.update(environment)
    result = subprocess.run(
        ["bash", str(tmp_path / "scripts" / SCRIPT.name), "--photo-only"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=12,
    )
    assert result.returncode == 2
    assert "MANUAL_UAT_CHECK=schema_revision status=RUNNING" in result.stdout
    assert f"reason={expected_reason}" in result.stderr
    assert "MANUAL_UAT_GATE=TECHNICAL_PASS" not in result.stdout
    assert "postgres://" not in result.stdout + result.stderr
