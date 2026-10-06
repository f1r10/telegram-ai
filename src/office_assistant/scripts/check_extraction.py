"""Small synthetic live structured-extraction smoke check."""

import asyncio

from office_assistant.ai.provider import OpenAILanguageModel
from office_assistant.core.settings import load_settings
from office_assistant.extraction.schemas import StructuredExtractionResult
from office_assistant.extraction.service import EXTRACTION_POLICY


async def run() -> None:
    settings = load_settings()
    if settings.openai_api_key is None:
        raise SystemExit("OPENAI_API_KEY is not configured")
    model = OpenAILanguageModel(
        settings.openai_api_key.get_secret_value(),
        default_model=settings.structured_extraction_model,
        strong_model=settings.openai_strong_model,
        timeout_seconds=settings.openai_request_timeout_seconds,
        max_output_tokens=settings.structured_extraction_max_output_tokens,
    )
    try:
        response = await model.generate(
            instructions=EXTRACTION_POLICY,
            input_messages=[
                {
                    "role": "user",
                    "content": (
                        '[SOURCE S1]\nlocator: {"kind":"synthetic"}\ncontent:\n'
                        "ABC MMC üçün KA45 qiyməti 125 AZN razılaşdırılıb."
                    ),
                }
            ],
            output_schema=StructuredExtractionResult,
            request_type="structured_extraction_smoke",
            user_id=0,
        )
        parsed = response.parsed
        if not isinstance(parsed, StructuredExtractionResult):
            raise SystemExit("Unexpected structured response")
        source_refs_valid = all(set(item.source_refs) <= {"S1"} for item in parsed.candidates)
        print("Structured extraction API işləyir")
        print(f"Model: {response.model}")
        print(f"Namizəd sayı: {len(parsed.candidates)}")
        print("Tiplər: " + ", ".join(item.candidate_type for item in parsed.candidates))
        print(f"Mənbələr düzgündür: {source_refs_valid}")
        print(f"Tokenlər: {response.usage.total_tokens}")
    finally:
        await model.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
