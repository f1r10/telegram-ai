"""Guard the Kali visual acceptance rollout from unsafe stack mutations."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/deploy_photo_staging.sh"


def test_photo_staging_script_is_syntax_valid_and_non_destructive() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    body = SCRIPT.read_text(encoding="utf-8")
    assert "docker-compose.staging.yml" in body
    assert "--no-deps --force-recreate app worker" in body
    assert "prepare_visual_catalog" in body
    assert "check_three_acceptance_gates" in body
    assert "git merge --ff-only" in body
    assert "git status --porcelain --untracked-files=no" in body
    assert "PHOTO_STAGING=MANUAL_PHOTO_UAT_REQUIRED" in body
    assert "schema_migration_required" in body
    assert "SELECT version_num FROM alembic_version" in body
    assert "compose exec -T postgres" in body
    assert '[[ "$status" == "running" ]]' in body
    assert '[[ "$health" == "none" || "$health" == "healthy" ]]' in body
    assert "_not_running" in body
    assert "_unhealthy" in body
    assert "for service in app worker; do" in body
    for unsafe in (
        "docker compose down",
        "docker volume rm",
        "docker system prune",
        "alembic upgrade",
        "--download-model",
        "VISUAL_SEMANTIC_ENABLED=true",
        "git reset --hard",
    ):
        assert unsafe not in body


def test_staging_semantic_model_is_off_by_default() -> None:
    compose = (ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")
    assert "${VISUAL_SEMANTIC_ENABLED:-true}" not in compose
    assert "${VISUAL_SEMANTIC_ENABLED:-false}" in compose


def test_photo_staging_rejects_unknown_mode_without_docker() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--unsafe-mode"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "reason=invalid_mode" in result.stderr
