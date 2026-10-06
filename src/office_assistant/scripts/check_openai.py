"""Explicit, small live probe using the same settings and adapter as the bot."""

import asyncio
import logging

from openai import APIStatusError

from office_assistant.ai.provider import OpenAILanguageModel, error_code
from office_assistant.ai.schemas import GeneralModelOutput, GroundedModelOutput
from office_assistant.core.settings import load_settings


async def check() -> int:
    settings = load_settings()
    if settings.openai_api_key is None:
        print("OPENAI_CHECK=NOT_CONFIGURED")
        return 1
    provider = OpenAILanguageModel(
        settings.openai_api_key.get_secret_value(),
        default_model=settings.openai_default_model,
        strong_model=settings.openai_strong_model,
        timeout_seconds=settings.openai_request_timeout_seconds,
        max_output_tokens=128,
        max_retries=0,
    )
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
            print(f"OPENAI_CHECK=OK schema={schema.__name__} model={result.model}")
        return 0
    except APIStatusError as error:
        print(f"OPENAI_CHECK=FAILED status={error.status_code} code={error_code(error)}")
        return 1
    except Exception as error:
        # SDK exception text can contain authentication headers or request content.
        print(f"OPENAI_CHECK=FAILED error_type={type(error).__name__}")
        return 1
    finally:
        await provider.close()


def main() -> None:
    logging.disable(logging.CRITICAL)
    raise SystemExit(asyncio.run(check()))


if __name__ == "__main__":
    main()
