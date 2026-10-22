"""Select one approved AI provider. Never silently send data to another vendor."""

from office_assistant.ai.provider import GeminiLanguageModel, OpenAILanguageModel
from office_assistant.core.settings import Settings


def create_language_model(
    settings: Settings,
    *,
    for_extraction: bool = False,
    diagnostic: bool = False,
) -> OpenAILanguageModel | GeminiLanguageModel | None:
    """None means no configured key; callers must degrade without AI guesses."""
    timeout_seconds = settings.openai_request_timeout_seconds
    max_output_tokens = (
        128
        if diagnostic
        else settings.structured_extraction_max_output_tokens
        if for_extraction
        else settings.openai_max_output_tokens
    )
    max_retries = 0 if diagnostic else 2
    if settings.ai_provider == "gemini":
        if (
            settings.gemini_api_key is None
            or not settings.gemini_api_key.get_secret_value().strip()
        ):
            return None
        return GeminiLanguageModel(
            settings.gemini_api_key.get_secret_value(),
            default_model=settings.gemini_model,
            vision_model=settings.gemini_vision_model,
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
            max_retries=max_retries,
        )
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value().strip():
        return None
    return OpenAILanguageModel(
        settings.openai_api_key.get_secret_value(),
        default_model=(
            settings.structured_extraction_model
            if for_extraction
            else settings.openai_default_model
        ),
        strong_model=settings.openai_strong_model,
        vision_model=settings.vision_model,
        **limits,
    )
