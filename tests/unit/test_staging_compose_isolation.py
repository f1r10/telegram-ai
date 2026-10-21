"""Prevent an isolated WSL staging stack from taking ownership of legacy data.

These checks read only version-controlled files; Docker config is also validated
in the GitHub Actions Compose job with synthetic environment variables.
"""

import re
import subprocess
from pathlib import Path
from shutil import which

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEGACY = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
STAGING = (ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")


def test_isolated_compose_uses_separate_project_containers_network_and_volumes() -> None:
    assert re.search(r"^name: qoder_ai_assistand$", LEGACY, re.M)
    assert re.search(r"^name: tgstage_ai_assistand$", STAGING, re.M)
    assert set(re.findall(r"^    container_name: (.+)$", LEGACY, re.M)).isdisjoint(
        re.findall(r"^    container_name: (.+)$", STAGING, re.M)
    )
    assert len(re.findall(r"^    container_name:", LEGACY, re.M)) == 7
    assert len(re.findall(r"^    container_name:", STAGING, re.M)) == 7
    assert set(re.findall(r"^    name: (qoder_\w+)$", LEGACY, re.M)).isdisjoint(
        re.findall(r"^    name: (tgstage_\w+)$", STAGING, re.M)
    )
    assert "name: tgstage_net" in STAGING
    assert "name: qoder_net" not in STAGING
    assert "qoder_postgres_data" not in STAGING
    assert "qoder_qdrant_data" not in STAGING


def test_staging_host_ports_and_unsafe_write_defaults_do_not_overlap_legacy() -> None:
    for line in (
        '"127.0.0.1:55435:5432"',
        '"127.0.0.1:16335:6333"',
        '"127.0.0.1:18095:8080"',
        '"127.0.0.1:${STAGING_MINIAPP_HOST_PORT:-18793}:${MINIAPP_LOCAL_PORT:-18791}"',
    ):
        assert line in STAGING
    for port in (5435, 6335, 18085):
        assert f'"127.0.0.1:{port}:' not in STAGING
    assert 'MINIAPP_ENABLED: ${MINIAPP_ENABLED:-false}' in STAGING
    assert 'TELEGRAM_POLLING_ENABLED: ${TELEGRAM_POLLING_ENABLED:-false}' in STAGING
    for flag in (
        "MINIAPP_STOCK_TRANSFER_ENABLED",
        "MINIAPP_STOCK_ACTIONS_ENABLED",
        "MINIAPP_STOCK_XLSX_ENABLED",
    ):
        assert f"{flag}: ${{{flag}:-false}}" in STAGING


@pytest.mark.skipif(which("bash") is None, reason="bash is not available")
def test_dedicated_staging_preflight_is_readonly_and_syntax_valid(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "preflight_isolated_staging.sh"
    subprocess.run(["bash", "-n", str(script)], check=True, timeout=10)
    result = subprocess.run(
        ["bash", str(script)], cwd=tmp_path, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 2
    assert "RESULT STAGING_BLOCKED" in result.stdout
    assert not list(tmp_path.iterdir())
