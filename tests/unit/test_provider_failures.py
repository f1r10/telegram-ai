import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import httpx2
import pytest
from openai import APIStatusError, AsyncOpenAI
from pydantic import ValidationError

from office_assistant.ai.provider import OpenAILanguageModel
from office_assistant.ai.schemas import GeneralModelOutput, GroundedModelOutput


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("code", ["credit_balance_exhausted", "insufficient_quota"])
def test_quota_errors_are_not_retried_or_logged_verbatim(
    nested: bool,
    code: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = {"code": code, "message": "sk-private-test-value"}
    error = APIStatusError(
        "sk-private-test-value",
        body={"error": body} if nested else body,
        response=httpx2.Response(429, request=httpx2.Request("POST", "https://api.openai.com")),
    )
    parse = AsyncMock(side_effect=error)
    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    adapter = OpenAILanguageModel(
        "unused", default_model="test", strong_model="unused", client=cast(AsyncOpenAI, client)
    )
    with pytest.raises(APIStatusError):
        asyncio.run(
            adapter.generate(
                instructions="policy",
                input_messages=[],
                output_schema=GeneralModelOutput,
                request_type="general",
                user_id=1,
            )
        )
    assert parse.await_count == 1
    assert code in caplog.text
    assert "sk-private-test-value" not in caplog.text


def test_transient_provider_failure_retries_same_model(monkeypatch: pytest.MonkeyPatch) -> None:
    error = APIStatusError(
        "unavailable",
        body={},
        response=httpx2.Response(503, request=httpx2.Request("POST", "https://api.openai.com")),
    )
    response = SimpleNamespace(
        output=[SimpleNamespace(content=[SimpleNamespace(parsed=GeneralModelOutput(answer="OK"))])]
    )
    parse = AsyncMock(side_effect=[error, response])
    monkeypatch.setattr("office_assistant.ai.provider.asyncio.sleep", AsyncMock())
    adapter = OpenAILanguageModel(
        "unused",
        default_model="test",
        strong_model="unused",
        client=cast(AsyncOpenAI, SimpleNamespace(responses=SimpleNamespace(parse=parse))),
    )
    result = asyncio.run(
        adapter.generate(
            instructions="policy",
            input_messages=[],
            output_schema=GeneralModelOutput,
            request_type="general",
            user_id=1,
        )
    )
    assert result.parsed == GeneralModelOutput(answer="OK")
    assert [call.kwargs["model"] for call in parse.await_args_list] == ["test", "test"]


def test_provider_strips_key_before_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    constructor = Mock()
    monkeypatch.setattr("office_assistant.ai.provider.AsyncOpenAI", constructor)
    OpenAILanguageModel("  test-key\r\n", default_model="test", strong_model="unused")
    assert constructor.call_args.kwargs["api_key"] == "test-key"


def test_web_synthesis_uses_larger_output_budget() -> None:
    response = SimpleNamespace(
        output=[SimpleNamespace(content=[SimpleNamespace(parsed=GroundedModelOutput(answer="OK"))])]
    )
    parse = AsyncMock(return_value=response)
    adapter = OpenAILanguageModel(
        "unused",
        default_model="test",
        strong_model="unused",
        max_output_tokens=700,
        client=cast(AsyncOpenAI, SimpleNamespace(responses=SimpleNamespace(parse=parse))),
    )
    asyncio.run(
        adapter.generate(
            instructions="policy",
            input_messages=[],
            output_schema=GroundedModelOutput,
            request_type="internet_synthesis",
            user_id=1,
        )
    )
    assert parse.await_args is not None
    assert parse.await_args.kwargs["max_output_tokens"] == 1400


def test_structured_validation_failure_retries_with_expanded_output_budget() -> None:
    invalid = ValidationError.from_exception_data(
        "GroundedModelOutput",
        [
            {
                "type": "json_invalid",
                "loc": (),
                "input": '{"answer": "truncated',
                "ctx": {"error": "EOF while parsing a string"},
            }
        ],
    )
    response = SimpleNamespace(
        output=[SimpleNamespace(content=[SimpleNamespace(parsed=GroundedModelOutput(answer="OK"))])]
    )
    parse = AsyncMock(side_effect=[invalid, response])
    adapter = OpenAILanguageModel(
        "unused",
        default_model="test",
        strong_model="unused",
        max_output_tokens=700,
        client=cast(AsyncOpenAI, SimpleNamespace(responses=SimpleNamespace(parse=parse))),
    )
    result = asyncio.run(
        adapter.generate(
            instructions="policy",
            input_messages=[],
            output_schema=GroundedModelOutput,
            request_type="internet_synthesis",
            user_id=1,
        )
    )
    assert result.parsed == GroundedModelOutput(answer="OK")
    assert [call.kwargs["max_output_tokens"] for call in parse.await_args_list] == [1400, 2800]
