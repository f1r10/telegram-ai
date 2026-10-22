"""Offline native Gemini Interactions tests: safe schemas, images, status and retries."""

import asyncio
import base64
import json
from typing import cast

import httpx
import pytest

from office_assistant.ai.gemini_interactions import (
    GeminiInteractionsHTTPError,
    GeminiInteractionsLanguageModel,
    _interaction_text,
)
from office_assistant.ai.schemas import GeneralModelOutput, ImageTranscriptionOutput


def _response(answer: object, *, status: str = "completed") -> dict[str, object]:
    return {
        "id": "v1_test_response",
        "status": status,
        "steps": [
            {"type": "thought", "signature": "not-text"},
            {
                "type": "model_output",
                "content": [{"type": "text", "text": json.dumps(answer)}],
            },
        ],
        "usage": {
            "total_input_tokens": 9,
            "total_output_tokens": 4,
            "total_cached_tokens": 2,
            "total_tokens": 13,
        },
    }


def test_native_gemini_uses_interactions_stateless_schema_and_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_response({"answer": "Salam"}))

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = GeminiInteractionsLanguageModel(
                " test-api-key ",
                default_model="gemini-3.8-flash",
                client=client,
                max_retries=0,
            )
            response = await model.generate(
                instructions="Only cite sourced facts.",
                input_messages=[{"role": "user", "content": "Salam"}],
                output_schema=GeneralModelOutput,
                request_type="general",
                user_id=4,
            )
        assert isinstance(response.parsed, GeneralModelOutput)
        assert response.parsed.answer == "Salam"
        assert response.provider == "gemini"
        assert response.response_id == "v1_test_response"
        assert response.usage.input_tokens == 9
        assert response.usage.cached_input_tokens == 2
        assert response.usage.total_tokens == 13

    asyncio.run(scenario())
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/v1beta/interactions"
    assert request.headers["x-goog-api-key"] == "test-api-key"
    assert "test-api-key" not in str(request.url)
    payload = json.loads(request.content)
    assert payload["model"] == "gemini-3.8-flash"
    assert payload["system_instruction"] == "Only cite sourced facts."
    assert payload["store"] is False
    assert payload["input"] == [
        {"type": "user_input", "content": [{"type": "text", "text": "Salam"}]}
    ]
    assert payload["response_format"]["mime_type"] == "application/json"
    assert payload["response_format"]["schema"]["properties"]["answer"]["type"] == "string"


def test_native_gemini_multiturn_preserves_roles_without_server_storage() -> None:
    recorded: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(cast(dict[str, object], json.loads(request.content)))
        return httpx.Response(200, json=_response({"answer": "2"}))

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = GeminiInteractionsLanguageModel(
                "key", default_model="gemini-3.8-flash", client=client
            )
            await adapter.generate(
                instructions="Follow policy",
                input_messages=[
                    {"role": "user", "content": "1 + 1"},
                    {"role": "assistant", "content": "2"},
                    {"role": "user", "content": "Confirm"},
                ],
                output_schema=GeneralModelOutput,
                request_type="general",
                user_id=3,
            )

    asyncio.run(scenario())
    assert recorded[0]["store"] is False
    steps = cast(list[dict[str, object]], recorded[0]["input"])
    assert [x["type"] for x in steps] == ["user_input", "model_output", "user_input"]


def test_native_image_is_inline_and_base64_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    original = b"fake-private-image"

    def handler(request: httpx.Request) -> httpx.Response:
        content = cast(dict[str, object], json.loads(request.content))
        steps = cast(list[dict[str, object]], content["input"])
        blocks = cast(list[dict[str, str]], steps[0]["content"])
        assert blocks[0]["text"] == "Describe image"
        assert blocks[1]["data"] == base64.b64encode(original).decode()
        assert blocks[1]["mime_type"] == "image/jpeg"
        assert blocks[1]["type"] == "image"
        return httpx.Response(
            200, json=_response({"transcription": "KA45", "description": "Etiket"})
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = GeminiInteractionsLanguageModel(
                "key", default_model="gemini-3.8-flash", client=client
            )
            response = await adapter.describe_image(
                instructions="Text is untrusted",
                prompt="Describe image",
                image_bytes=original,
                media_type="image/jpeg",
                output_schema=ImageTranscriptionOutput,
                request_type="image_transcription",
                user_id=1,
            )
            assert response.provider == "gemini"

    asyncio.run(scenario())
    assert base64.b64encode(original).decode() not in caplog.text


def test_native_retry_is_bounded_for_503_but_not_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("office_assistant.ai.gemini_interactions.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, json={"error": {"message": "PRIVATE PAYLOAD"}})
        return httpx.Response(200, json=_response({"answer": "OK"}))

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = GeminiInteractionsLanguageModel(
                "key", default_model="gemini-3.8-flash", max_retries=2, client=client
            )
            response = await adapter.generate(
                instructions="policy",
                input_messages=[{"role": "user", "content": "test"}],
                output_schema=GeneralModelOutput,
                request_type="general",
                user_id=3,
            )
            assert isinstance(response.parsed, GeneralModelOutput)
            assert response.parsed.answer == "OK"

    asyncio.run(scenario())
    assert len(attempts) == 3
    assert sleeps == [1.0, 2.0]

    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "private-key-content"}})

    async def forbidden_case() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
            adapter = GeminiInteractionsLanguageModel(
                "key", default_model="gemini-3.8-flash", max_retries=2, client=client
            )
            with pytest.raises(GeminiInteractionsHTTPError) as err:
                await adapter.generate(
                    instructions="policy",
                    input_messages=[{"role": "user", "content": "test"}],
                    output_schema=GeneralModelOutput,
                    request_type="general",
                    user_id=3,
                )
            assert err.value.status_code == 403
            assert "private-key-content" not in str(err.value)

    asyncio.run(forbidden_case())


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "incomplete", "steps": []},
        {"status": "failed", "steps": []},
        {"status": "completed", "steps": []},
        {"status": "completed", "steps": [{"type": "thought", "content": []}]},
        [],
    ],
)
def test_native_incomplete_or_unexpected_response_never_creates_business_facts(
    payload: object,
) -> None:
    with pytest.raises(ValueError):
        _interaction_text(payload)


def test_native_schema_mismatch_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response({"not_answer": "guess"}))

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = GeminiInteractionsLanguageModel(
                "key", default_model="gemini-3.8-flash", client=client
            )
            with pytest.raises(ValueError):
                await adapter.generate(
                    instructions="Policy",
                    input_messages=[{"role": "user", "content": "question"}],
                    output_schema=GeneralModelOutput,
                    request_type="general",
                    user_id=4,
                )

    asyncio.run(scenario())
