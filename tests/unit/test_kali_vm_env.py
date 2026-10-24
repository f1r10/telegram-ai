"""Offline safety contracts for an independent, first-run Kali Linux staging VM."""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from office_assistant.scripts.init_kali_vm_env import generate_environment

ROOT = Path(__file__).resolve().parents[2]


def test_init_creates_independent_safe_openai_environment() -> None:
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    result = generate_environment(
        template,
        secrets_by_key={"POSTGRES_PASSWORD": "long-test-pg", "SEARXNG_SECRET": "long-test-sx"},
    )
    lines = dict(
        item.split("=", 1) for item in result.splitlines() if "=" in item and not item.startswith("#")
    )
    assert lines["APP_ENV"] == "staging"
    assert lines["POSTGRES_DB"] == "office_assistant_kali_test"
    assert lines["POSTGRES_PASSWORD"] == "long-test-pg"
    assert lines["SEARXNG_SECRET"] == "long-test-sx"
    assert lines["AI_PROVIDER"] == "openai"
    assert lines["OPENAI_API_KEY"] == ""
    assert lines["TELEGRAM_BOT_TOKEN"] == ""
    assert lines["TELEGRAM_ACCESS_PASSWORD"] == ""
    assert lines["OPENAI_DEFAULT_MODEL"] == "gpt-6-luna"
    assert lines["OPENAI_STRONG_MODEL"] == "gpt-6.1-sol"
    assert lines["GEMINI_API_KEY"] == ""
    for key in (
        "TELEGRAM_POLLING_ENABLED",
        "MINIAPP_ENABLED",
        "STRUCTURED_EXTRACTION_ENABLED",
        "MINIAPP_STOCK_TRANSFER_ENABLED",
        "MINIAPP_STOCK_ACTIONS_ENABLED",
        "MINIAPP_STOCK_XLSX_ENABLED",
        "VISUAL_SEMANTIC_ENABLED",
    ):
        assert lines[key] == "false"
    keys = [
        row.partition("=")[0]
        for row in result.splitlines()
        if "=" in row and not row.lstrip().startswith("#")
    ]
    assert len(keys) == len(set(keys))


def test_duplicate_and_missing_keys_fail_closed() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        generate_environment(
            "AI_PROVIDER=openai\nAI_PROVIDER=gemini\n",
            secrets_by_key={},
        )
    with pytest.raises(ValueError, match="missing in template"):
        generate_environment("AI_PROVIDER=openai\n", secrets_by_key={})


def test_cli_respects_existing_env_and_does_not_log_private_key(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "init_kali_vm_env.py"
    script.write_text(
        (ROOT / "scripts" / "init_kali_vm_env.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text(
        (ROOT / ".env.example").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    first = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=10)
    assert first.returncode == 0
    assert env_path.exists()
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert "POSTGRES_PASSWORD=" not in first.stdout
    assert "OPENAI_API_KEY=" not in first.stdout
    assert "KALI_STAGING_ENV_CREATED" in first.stdout
    before = env_path.read_bytes()
    second = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=10)
    assert second.returncode != 0
    assert env_path.read_bytes() == before
    assert "BLOCKED:" in second.stderr + second.stdout
    assert os.path.getsize(env_path) > 100
