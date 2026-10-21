"""WSL2 delivery preflight is syntax-checked and refuses non-repository paths.

The test never starts Docker or reads private credentials.
"""

import subprocess
from pathlib import Path
from shutil import which

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "preflight_wsl2.sh"

pytestmark = pytest.mark.skipif(which("bash") is None, reason="bash is not installed")


def test_wsl2_preflight_shell_syntax() -> None:
    subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_wsl2_preflight_does_not_read_or_print_secrets_outside_repo(
    tmp_path: Path,
) -> None:
    secret = "private-test-token-must-not-leak"
    local_env = tmp_path / ".env"
    local_env.write_text(f"TELEGRAM_BOT_TOKEN={secret}\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "RESULT BLOCKED" in result.stdout
    assert secret not in result.stdout + result.stderr
    assert local_env.read_text(encoding="utf-8") == f"TELEGRAM_BOT_TOKEN={secret}\n"
