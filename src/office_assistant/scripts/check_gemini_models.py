"""Read-only Gemini model catalog probe. Never sends document or prompt content.

The API key is transmitted in the HTTP header over TLS, never in a URL or logs.
Reports only model IDs and safe HTTP/error categories.
"""

import asyncio
import json
import logging
from typing import cast

import httpx

from office_assistant.core.settings import load_settings

MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
RECOMMENDED = (
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
)


def available_generation_models(payload: object) -> set[str]:
    """Keep only published generation-capable model identifiers."""
    if not isinstance(payload, dict):
        raise ValueError("Malformed Gemini model catalog")
    fields = cast(dict[str, object], payload)
    rows = fields.get("models")
    if not isinstance(rows, list):
        raise ValueError("Malformed Gemini model catalog")
    result: set[str] = set()
    for row in cast(list[object], rows):
        if not isinstance(row, dict):
            continue
        model = cast(dict[str, object], row)
        name = model.get("name")
        methods = model.get("supportedGenerationMethods")
        if (
            isinstance(name, str)
            and name.startswith("models/gemini-")
            and isinstance(methods, list)
            and "generateContent" in methods
        ):
            result.add(name.removeprefix("models/"))
    return result


async def check() -> int:
    settings = load_settings()
    if settings.ai_provider != "gemini":
        print("GEMINI_MODELS=NOT_SELECTED")
        return 1
    if settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value().strip():
        print("GEMINI_MODELS=NOT_CONFIGURED")
        return 1
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            response = await client.get(
                MODELS_URL,
                headers={"x-goog-api-key": settings.gemini_api_key.get_secret_value()},
                params={"pageSize": 1000},
            )
            # Do not log HTTP response bodies, headers, URLs or any private credentials.
            if response.status_code != 200:
                print(f"GEMINI_MODELS=FAILED http_status={response.status_code}")
                return 1
            available = available_generation_models(response.json())
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as error:
        print(f"GEMINI_MODELS=FAILED error_type={type(error).__name__}")
        return 1
    print(f"GEMINI_MODELS=OK available_count={len(available)}")
    print(
        "GEMINI_SELECTED_MODEL="
        f"{settings.gemini_model} accessible={str(settings.gemini_model in available).lower()}"
    )
    for model_id in RECOMMENDED:
        if model_id in available:
            print(f"GEMINI_MODEL_AVAILABLE={model_id}")
    if not any(model_id in available for model_id in RECOMMENDED):
        for model_id in sorted(available)[:12]:
            print(f"GEMINI_MODEL_AVAILABLE={model_id}")
    return 0


def main() -> None:
    logging.disable(logging.CRITICAL)
    raise SystemExit(asyncio.run(check()))


if __name__ == "__main__":
    main()
