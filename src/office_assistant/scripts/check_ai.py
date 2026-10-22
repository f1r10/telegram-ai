"""Explicit live AI probe, selected provider only. Uses synthetic non-company text."""

import asyncio
import logging

from openai import APIStatusError

from office_assistant.ai.factory import create_language_model
from office_assistant.ai.gemini_interactions import GeminiInteractionsHTTPError
from office_assistant.ai.provider import error_code
from office_assistant.ai.schemas import GeneralModelOutput, GroundedModelOutput
from office_assistant.core.settings import load_settings


async def check() -> int:
    settings = load_settings()
    provider = create_language_model(settings, diagnostic=True)
    if provider is None:
        print(f"AI_CHECK=NOT_CONFIGURED provider={settings.ai_provider}")
        return 1
    try:
        for schema in (GeneralModelOutput, GroundedModelOutput):
            result = await provider.generate(
                instructions="Answer briefly. Use only the supplied source. Cite S1.",
                input_messages=[
                    {
                        "role": "user",
                        "content": "SOURCE S1: The test code is ALPHA. What is the test code?",
                    }
                ],
                output_schema=schema,
                request_type="diagnostic",
                user_id=0,
            )
            print(
                f"AI_CHECK=OK provider={result.provider} "
                f"schema={schema.__name__} model={result.model}"
            )
        return 0
    except GeminiInteractionsHTTPError as error:
        print(f"AI_CHECK=FAILED provider=gemini status={error.status_code}")
        if error.status_code == 503:
            print("AI_HINT=Gemini is temporarily overloaded; native Interactions retries exhausted")
        elif error.status_code == 404:
            print("AI_HINT=Check the selected model with check_gemini_models")
        return 1
    except APIStatusError as error:
        print(
            f"AI_CHECK=FAILED provider={settings.ai_provider} "
            f"status={error.status_code} code={error_code(error)}"
        )
        if settings.ai_provider == "gemini" and error.status_code == 404:
            print(
                "AI_HINT=Model might be unavailable to this project; "
                "run office_assistant.scripts.check_gemini_models"
            )
        return 1
    except Exception as error:
        # Never print SDK exception bodies (could include private prompt/headers).
        print(f"AI_CHECK=FAILED provider={settings.ai_provider} error_type={type(error).__name__}")
        return 1
    finally:
        await provider.close()


def main() -> None:
    logging.disable(logging.CRITICAL)
    raise SystemExit(asyncio.run(check()))


if __name__ == "__main__":
    main()
