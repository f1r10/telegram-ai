"""Vision calls stay behind the provider boundary and never leak image bytes."""

import asyncio
import base64
from collections.abc import Sequence
from types import SimpleNamespace
from typing import cast

import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel

from office_assistant.ai.provider import OpenAILanguageModel
from office_assistant.ai.schemas import GeneralModelOutput, ImageTranscriptionOutput

JPEG = b"\xff\xd8\xff\xe0" + b"pixels" * 8


class RecordingParse:
    """Stands in for `client.responses.parse` and keeps the exact keyword arguments."""

    def __init__(self, results: Sequence[object]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, object]] = []

    async def __call__(self, **kwargs: object) -> object:
        result = self._results[min(len(self.calls), len(self._results) - 1)]
        self.calls.append(kwargs)
        if isinstance(result, Exception):
            raise result
        return result


def _model(parsed: BaseModel) -> SimpleNamespace:
    return SimpleNamespace(output=[SimpleNamespace(content=[SimpleNamespace(parsed=parsed)])])


def _adapter(parse: RecordingParse, vision_model: str | None = None) -> OpenAILanguageModel:
    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    return OpenAILanguageModel(
        "unused",
        default_model="text-model",
        strong_model="strong-model",
        vision_model=vision_model,
        client=cast(AsyncOpenAI, client),
    )


def _describe(adapter: OpenAILanguageModel) -> None:
    asyncio.run(
        adapter.describe_image(
            instructions="policy",
            prompt="Transcribe this document.",
            image_bytes=JPEG,
            media_type="image/jpeg",
            output_schema=ImageTranscriptionOutput,
            request_type="image_transcription",
            user_id=7,
        )
    )


def test_describe_image_sends_the_vision_model_and_a_data_url_block() -> None:
    parsed = ImageTranscriptionOutput(transcription="KA45 — 125 AZN", description="Qiymət siyahısı")
    parse = RecordingParse([_model(parsed)])

    result = asyncio.run(
        _adapter(parse, "gpt-4o-mini").describe_image(
            instructions="policy",
            prompt="Transcribe this document.",
            image_bytes=JPEG,
            media_type="image/jpeg",
            output_schema=ImageTranscriptionOutput,
            request_type="image_transcription",
            user_id=7,
        )
    )

    assert result.parsed is parsed
    assert result.model == "gpt-4o-mini"
    call = parse.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert call["user"] == "7"
    blocks = cast("list[dict[str, object]]", call["input"])
    assert blocks[0]["role"] == "user"
    content = cast("list[dict[str, str]]", blocks[0]["content"])
    assert content[0] == {"type": "input_text", "text": "Transcribe this document."}
    assert content[1]["type"] == "input_image"
    expected = f"data:image/jpeg;base64,{base64.b64encode(JPEG).decode()}"
    assert content[1]["image_url"] == expected


def test_image_transcription_gets_the_larger_output_budget() -> None:
    parse = RecordingParse([_model(ImageTranscriptionOutput(transcription="", description="Boş"))])

    _describe(_adapter(parse, "vision"))

    assert cast(int, parse.calls[0]["max_output_tokens"]) >= 1400


def test_image_bytes_never_reach_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    parse = RecordingParse(
        [
            RuntimeError("provider exploded"),
            _model(ImageTranscriptionOutput(transcription="", description="Qara kvadrat")),
        ]
    )

    with pytest.raises(RuntimeError), caplog.at_level("INFO"):
        _describe(_adapter(parse, "vision"))

    assert base64.b64encode(JPEG).decode() not in caplog.text
    assert JPEG.hex() not in caplog.text


def test_vision_model_falls_back_to_the_default_model() -> None:
    parse = RecordingParse(
        [_model(ImageTranscriptionOutput(transcription="", description="Çərçivə"))]
    )

    _describe(_adapter(parse))

    assert parse.calls[0]["model"] == "text-model"


def test_text_generation_still_passes_plain_message_dicts() -> None:
    parse = RecordingParse([_model(GeneralModelOutput(answer="Salam"))])
    adapter = _adapter(parse)

    asyncio.run(
        adapter.generate(
            instructions="policy",
            input_messages=[{"role": "user", "content": "Salam"}],
            output_schema=GeneralModelOutput,
            request_type="general",
            user_id=1,
        )
    )

    assert parse.calls[0]["model"] == "text-model"
    assert parse.calls[0]["input"] == [{"role": "user", "content": "Salam"}]
    assert parse.calls[0]["max_output_tokens"] == 700
