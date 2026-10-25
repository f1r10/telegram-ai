"""Staging visual activation is syntax-valid and does not destroy DB volumes."""

from pathlib import Path
import subprocess


def test_staging_visual_rollout_shell_is_safe_and_syntax_valid() -> None:
    path = Path(__file__).resolve().parents[2] / "scripts/activate_catalog_visual_staging.sh"
    assert path.is_file()
    result = subprocess.run(
        ["bash", "-n", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    script = path.read_text(encoding="utf-8")
    assert "docker-compose.staging.yml" in script
    assert "catalog_visual_references" in script
    assert "--download-model --apply --limit 200" in script
    assert "diagnose_catalog_visual" in script
    assert "CATALOG_VISUAL_ACTIVATION=READY" in script
    assert "down -v" not in script
    assert "docker volume rm" not in script
    assert "--force-recreate app" in script
