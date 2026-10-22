"""Native Gemini Interactions REST adapter with stateless, schema-validated replies.

Uses the same v1beta/interactions endpoint that the owner's working genai SDK
example targets, not the OpenAI-compatible chat-completions gateway.
No files/prompts/keys are logged. No implicit fallback to another vendor.
"""

import asyncio
import base64
import logging
import time
from collections.abc import Sequence
from typing import TypeVar, cast

import httpx
from pydantic import BaseModel

from office_assistant.ai.provider import ModelResponse, TokenUsage

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)
INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}


class GeminiInteractionsHTTPError(Exception):
    """Safe HTTP failure carrying only its status, never raw request/response bodies."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Gemini Interactions HTTP status {status_code}")


def _as_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return cast(dict[str, object], value)


def _usage_count(value: object) -> int:
    """Only use nonnegative JSON integer counters, never coerce arbitrary objects."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def extract_interaction_text(payload: object) -> tuple[str, str | None, TokenUsage]:
    obj = _as_object(payload)
    if obj.get("status") != "completed":
        raise ValueError("Gemini interaction is not completed")
    steps = obj.get("steps")
    if not isinstance(steps, list):
        raise ValueError("Gemini response has no steps")
    output: list[str] = []
    for raw_step in cast(list[object], steps):
        step = _as_object(raw_step)
        if step.get("type") != "model_output":
            continue
        content = step.get("content")
        if not isinstance(content, list):
            continue
        for raw_block in cast(list[object], content):
            block = _as_object(raw_block)
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                output.append(cast(str, block["text"]))
    if not output:
        raise ValueError("Gemini interaction has no output text")
    usage = _as_object(obj.get("usage") or {})
    interaction_id = obj.get("id")
    return (
        "".join(output),
        interaction_id if isinstance(interaction_id, str) else None,
        TokenUsage(
            input_tokens=_usage_count(usage.get("total_input_tokens")),
            cached_input_tokens=_usage_count(usage.get("total_cached_tokens")),
            output_tokens=_usage_count(usage.get("total_output_tokens")),
            total_tokens=_usage_count(usage.get("total_tokens")),
        ),
    )


def _conversation_input(messages: Sequence[dict[str, str]]) -> list[dict[str, object]]:
    """Keep system policies separate; only known user/assistant roles are supported."""
    steps: list[dict[str, object]] = []
    for message in messages:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            raise ValueError("Unsupported Gemini message role")
        steps.append(
            {
                "type": "user_input" if role == "user" else "model_output",
                "content": [{"type": "text", "text": message["content"]}],
            }
        )
    if not steps:
        raise ValueError("No Gemini input")
    return steps


class GeminiInteractionsLanguageModel:
    """Validated Interactions model; bounded retries only for temporary failures."""

    provider_name = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        default_model: str,
        vision_model: str | None = None,
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 700,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.default_model = default_model
        self.vision_model = vision_model or default_model
        self.max_output_tokens = max_output_tokens
        self.max_retries = max_retries
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds, trust_env=False)
        self._owns_client = client is None
        self._api_key = api_key.strip()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate(
        self,
        *,
        instructions: str,
        input_messages: Sequence[dict[str, str]],
        output_schema: type[T],
        request_type: str,
        user_id: int,
    ) -> ModelResponse:
        del user_id
        return await self._generate(
            instructions=instructions,
            input_steps=_conversation_input(input_messages),
            output_schema=output_schema,
            request_type=request_type,
            model=self.default_model,
        )

    async def describe_image(
        self,
        *,
        instructions: str,
        prompt: str,
        image_bytes: bytes,
        media_type: str,
        output_schema: type[T],
        request_type: str,
        user_id: int,
    ) -> ModelResponse:
        del user_id
        if media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("Unsupported image media type")
        return await self._generate(
            instructions=instructions,
            input_steps=[
                {
                    "type": "user_input",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image",
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                            "mime_type": media_type,
                        },
                    ],
                }
            ],
            output_schema=output_schema,
            request_type=request_type,
            model=self.vision_model,
        )

    async def _generate(
        self,
        *,
        instructions: str,
        input_steps: list[dict[str, object]],
        output_schema: type[T],
        request_type: str,
        model: str,
    ) -> ModelResponse:
        started = time.perf_counter()
        token_budget = (
            max(self.max_output_tokens, 1400)
            if request_type
            in {
                "internet_synthesis",
                "combined_search_synthesis",
                "image_transcription",
            }
            else self.max_output_tokens
        )
        request: dict[str, object] = {
            "model": model,
            "system_instruction": instructions,
            "input": input_steps,
            "store": False,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": output_schema.model_json_schema(),
            },
            "generation_config": {"max_output_tokens": token_budget},
        }
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.post(
                    INTERACTIONS_URL,
                    headers={"x-goog-api-key": self._api_key},
                    json=request,
                )
                if response.status_code != 200:
                    if response.status_code in TRANSIENT_STATUSES and attempt < self.max_retries:
                        logger.warning(
                            "Gemini temporarily unavailable | status=%s attempt=%s",
                            response.status_code,
                            attempt + 1,
                        )
                        await asyncio.sleep(
                            _backoff_seconds(attempt, response.headers.get("retry-after"))
                        )
                        continue
                    raise GeminiInteractionsHTTPError(response.status_code)
                text, interaction_id, usage = extract_interaction_text(response.json())
                parsed = output_schema.model_validate_json(text)
                return ModelResponse(
                    parsed=parsed,
                    model=model,
                    response_id=interaction_id,
                    usage=usage,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    provider=self.provider_name,
                )
            except (httpx.TimeoutException, httpx.ConnectError) as error:
                if attempt >= self.max_retries:
                    raise
                logger.warning(
                    "Gemini network retry | error_type=%s attempt=%s",
                    type(error).__name__,
                    attempt + 1,
                )
                await asyncio.sleep(_backoff_seconds(attempt, None))
        raise RuntimeError("Gemini Interactions did not return")


def _backoff_seconds(attempt: int, retry_after: str | None) -> float:
    """Clamp Retry-After so Telegram replies cannot wait indefinitely."""
    if retry_after and retry_after.isdecimal():
        return min(float(retry_after), 4.0)
    return float(min(2**attempt, 4))
