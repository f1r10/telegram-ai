#!/usr/bin/env python3
"""Initialize a fresh, independent Kali VM staging environment without secrets in logs.

Run only after the read-only staging preflight. This command will refuse to
overwrite an existing .env, uses mode 0600 and never prints generated secrets.
"""

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".env.example"
DESTINATION = ROOT / ".env"

SAFE_SETTINGS = {
    "APP_ENV": "staging",
    "LOG_LEVEL": "INFO",
    "POSTGRES_DB": "office_assistant_kali_test",
    "POSTGRES_USER": "office_kali_test",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_ACCESS_PASSWORD": "",
    "TELEGRAM_POLLING_ENABLED": "false",
    "AI_PROVIDER": "openai",
    "OPENAI_API_KEY": "",
    "OPENAI_DEFAULT_MODEL": "gpt-6-luna",
    "OPENAI_STRONG_MODEL": "gpt-6.1-sol",
    "STRUCTURED_EXTRACTION_MODEL": "gpt-6-luna",
    "VISION_MODEL": "gpt-4o-mini",
    "GEMINI_API_KEY": "",
    "GEMINI_MODEL": "",
    "GEMINI_VISION_MODEL": "",
    "MINIAPP_ENABLED": "false",
    "MINIAPP_STOCK_TRANSFER_ENABLED": "false",
    "MINIAPP_STOCK_ACTIONS_ENABLED": "false",
    "MINIAPP_STOCK_XLSX_ENABLED": "false",
    "VISUAL_SEMANTIC_ENABLED": "false",
    "STRUCTURED_EXTRACTION_ENABLED": "false",
}


def generate_environment(template: str, *, secrets_by_key: dict[str, str]) -> str:
    """Produce one value for each template key; avoid duplicate overrides."""
    values = {**SAFE_SETTINGS, **secrets_by_key}
    known: set[str] = set()
    result: list[str] = []
    for line in template.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            result.append(line)
            continue
        key, _, original = line.partition("=")
        if key in known:
            raise ValueError(f"Duplicate environment key in template: {key}")
        known.add(key)
        result.append(f"{key}={values.get(key, original)}")
    missing = sorted(set(values) - known)
    if missing:
        raise ValueError(f"Expected settings missing in template: {', '.join(missing)}")
    return "\n".join(result) + "\n"


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit("BLOCKED: .env already exists; no changes made")
    template = TEMPLATE.read_text(encoding="utf-8")
    result = generate_environment(
        template,
        secrets_by_key={
            "POSTGRES_PASSWORD": secrets.token_hex(32),
            "SEARXNG_SECRET": secrets.token_hex(32),
        },
    )
    previous_umask = os.umask(0o077)
    try:
        # No overwrite even if file appears between the check and the open().
        handle = os.open(DESTINATION, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(result)
    finally:
        os.umask(previous_umask)
    print("KALI_STAGING_ENV_CREATED | mode=0600 | OpenAI key not set | bot polling OFF")


if __name__ == "__main__":
    main()
