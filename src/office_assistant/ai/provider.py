"""Narrow OpenAI Responses API adapter."""

# The SDK exposes generated request unions that are stricter than the small
# provider-neutral message boundary used by this application.
# pyright: reportArgumentType=false, reportUnknownMemberType=false

import asyncio
import base64
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, cast

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ModelResponse:
    parsed: BaseModel
    model: str
    response_id: str | None
    usage: TokenUsage
    latency_ms: int


class LanguageModel(Protocol):
    async def generate(
        self,
        *,
        instructions: str,
        input_messages: Sequence[dict[str, str]],
        output_schema: type[T],
        request_type: str,
        user_id: int,
    ) -> ModelResponse: ...


class VisionLanguageModel(Protocol):
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
    ) -> ModelResponse: ...


class OpenAILanguageModel:
    """Reusable Responses API client hidden behind the application protocol."""

    provider_name = "openai"

    def __init__(
        self,
        api_key: str,
        *,
        default_model: str,
        strong_model: str,
        vision_model: str | None = None,
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 700,
        max_retries: int = 2,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.default_model = default_model
        self.strong_model = strong_model
        self.vision_model = vision_model or default_model
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.max_retries = max_retries
        self._client = client or AsyncOpenAI(
            api_key=api_key.strip(), timeout=timeout_seconds, max_retries=0
        )

    async def close(self) -> None:
        await self._client.close()

    async def generate(
        self,
        *,
        instructions: str,
        input_messages: Sequence[dict[str, str]],
        output_schema: type[T],
        request_type: str,
        user_id: int,
    ) -> ModelResponse:
        return await self._generate_parsed(
            model=self.default_model,
            instructions=instructions,
            input_blocks=list(input_messages),
            output_schema=output_schema,
            request_type=request_type,
            user_id=user_id,
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
        data_url = f"data:{media_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        return await self._generate_parsed(
            model=self.vision_model,
            instructions=instructions,
            input_blocks=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": data_url, "detail": "auto"},
                    ],
                }
            ],
            output_schema=output_schema,
            request_type=request_type,
            user_id=user_id,
        )

    async def _generate_parsed(
        self,
        *,
        model: str,
        instructions: str,
        input_blocks: list[dict[str, Any]],
        output_schema: type[T],
        request_type: str,
        user_id: int,
    ) -> ModelResponse:
        started = time.perf_counter()
        output_token_limit = _output_token_limit(self.max_output_tokens, request_type)
        for attempt in range(self.max_retries + 1):
            try:
                response: Any = await self._parse_once(
                    model=model,
                    instructions=instructions,
                    input_blocks=input_blocks,
                    output_schema=output_schema,
                    request_type=request_type,
                    user_id=user_id,
                    max_output_tokens=output_token_limit,
                )
                parsed = _parsed_value(response)
                if not isinstance(parsed, output_schema):
                    raise ValueError("Responses API returned no valid structured output")
                return ModelResponse(
                    parsed=parsed,
                    model=model,
                    response_id=getattr(response, "id", None),
                    usage=_usage(response),
                    latency_ms=round((time.perf_counter() - started) * 1000),
                )
            except ValidationError as error:
                expanded_limit = _expanded_output_token_limit(output_token_limit)
                if expanded_limit > output_token_limit:
                    logger.warning(
                        "OpenAI structured output invalid; retrying with larger output limit | "
                        "operation=%s model=%s error_type=%s",
                        _safe_label(request_type),
                        _safe_label(model),
                        type(error).__name__,
                    )
                    try:
                        response = await self._parse_once(
                            model=model,
                            instructions=instructions,
                            input_blocks=input_blocks,
                            output_schema=output_schema,
                            request_type=request_type,
                            user_id=user_id,
                            max_output_tokens=expanded_limit,
                        )
                        parsed = _parsed_value(response)
                        if not isinstance(parsed, output_schema):
                            raise ValueError("Responses API returned no valid structured output")
                        return ModelResponse(
                            parsed=parsed,
                            model=model,
                            response_id=getattr(response, "id", None),
                            usage=_usage(response),
                            latency_ms=round((time.perf_counter() - started) * 1000),
                        )
                    except ValidationError:
                        logger.exception(
                            "OpenAI structured output remained invalid | operation=%s model=%s",
                            _safe_label(request_type),
                            _safe_label(model),
                        )
                        raise
                logger.error(
                    "OpenAI structured output invalid | operation=%s model=%s error_type=%s",
                    _safe_label(request_type),
                    _safe_label(model),
                    type(error).__name__,
                )
                raise
            except (APIConnectionError, APITimeoutError, APIStatusError) as error:
                retry = _retryable(error) and attempt < self.max_retries
                logger.warning(
                    "OpenAI request failed | operation=%s model=%s attempt=%s retry=%s "
                    "error_type=%s status=%s code=%s request_id=%s",
                    _safe_label(request_type),
                    _safe_label(model),
                    attempt + 1,
                    retry,
                    type(error).__name__,
                    getattr(error, "status_code", None),
                    error_code(error),
                    _safe_label(getattr(error, "request_id", None)),
                )
                if not retry:
                    raise
                delay = min(2**attempt, 4)
                await asyncio.sleep(delay)
            except Exception as error:
                logger.error("OpenAI output failed | error_type=%s", type(error).__name__)
                raise
        raise RuntimeError("OpenAI generation did not return")

    async def _parse_once(
        self,
        *,
        model: str,
        instructions: str,
        input_blocks: list[dict[str, Any]],
        output_schema: type[T],
        request_type: str,
        user_id: int,
        max_output_tokens: int,
    ) -> Any:
        del request_type
        return await self._client.responses.parse(
            model=model,
            instructions=instructions,
            input=input_blocks,
            text_format=output_schema,
            max_output_tokens=max_output_tokens,
            store=False,
            user=str(user_id),
        )


def _parsed_value(response: Any) -> BaseModel | None:
    for item in getattr(response, "output", []):
        for content in getattr(item, "content", []):
            parsed = getattr(content, "parsed", None)
            if isinstance(parsed, BaseModel):
                return parsed
    return None


def _usage(response: Any) -> TokenUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    details = getattr(usage, "input_tokens_details", None)
    return TokenUsage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        cached_input_tokens=int(getattr(details, "cached_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
    )


def _safe_label(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value):
        return value
    return "unknown"


def error_code(error: Exception) -> str:
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        fields = cast(dict[str, object], body)
        details = fields.get("error", fields)
        if isinstance(details, dict):
            fields = cast(dict[str, object], details)
            return _safe_label(fields.get("code") or fields.get("type"))
    return "unknown"


def _output_token_limit(configured_limit: int, request_type: str) -> int:
    if request_type in {"internet_synthesis", "combined_search_synthesis", "image_transcription"}:
        return max(configured_limit, 1400)
    return configured_limit


def _expanded_output_token_limit(current_limit: int) -> int:
    return min(max(current_limit * 2, current_limit + 600), 2800)


def _retryable(error: Exception) -> bool:
    if isinstance(error, (APIConnectionError, APITimeoutError)):
        return True
    if not isinstance(error, APIStatusError):
        return False
    if error.status_code == 429:
        return error_code(error) not in {"insufficient_quota", "credit_balance_exhausted"}
    return error.status_code in {
        408,
        409,
        500,
        502,
        503,
        504,
    }
