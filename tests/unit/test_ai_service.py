import asyncio
from types import SimpleNamespace

from office_assistant.ai.provider import ModelResponse, OpenAILanguageModel, TokenUsage
from office_assistant.ai.schemas import GroundedModelOutput
from office_assistant.ai.service import (
    GROUNDED_POLICY,
    NOT_FOUND_AZ,
    GroundedAnswerService,
    RetrievalUnavailable,
)
from office_assistant.ai.sources import build_source_pack
from office_assistant.infrastructure.database.models import AIMessage
from office_assistant.knowledge.models import RetrievedChunk


class _FakeRetrieval:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks

    async def search_internal_knowledge(
        self, *args: object, **kwargs: object
    ) -> list[RetrievedChunk]:
        return self.chunks


class _FakeModel:
    def __init__(self, output: GroundedModelOutput) -> None:
        self.output = output
        self.calls = 0

    async def generate(self, **kwargs: object) -> ModelResponse:
        self.calls += 1
        return ModelResponse(self.output, "test-model", "resp-1", TokenUsage(1, 0, 2, 3), 4)


class _MemoryGroundedService(GroundedAnswerService):
    def __init__(self, retrieval: object, model: object) -> None:
        self.retrieval = retrieval  # type: ignore[assignment]
        self.language_model = model  # type: ignore[assignment]
        self.retrieval_limit = 5
        self.max_context_tokens = 3200
        self.history_max_messages = 6
        self.history_max_tokens = 1200
        self.max_sources = 5
        self.saved: list[tuple[str, str]] = []

    async def _history(self, user_id: int, mode: str) -> list[AIMessage]:
        return []

    async def _save_message(self, user_id: int, mode: str, role: str, content: str) -> None:
        self.saved.append((role, content))

    async def _save_usage(self, user_id: int, mode: str, response: ModelResponse) -> None:
        return None


def _chunk(locator: dict[str, object] | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="a" * 32,
        text="KA45 məhsulunun zəmanət müddəti 24 aydır.",
        source_filename="2026_kataloq.pdf",
        source_file_id=45,
        parse_run_id=1,
        locator=locator or {"blocks": [{"page": 17}]},
        retrieval_method="hybrid",
        score=0.9,
        rank=1,
    )


def _chunk_with_name(name: str, locator: dict[str, object]) -> RetrievedChunk:
    chunk = _chunk(locator)
    return RetrievedChunk(
        chunk_id=name.ljust(32, "b")[:32],
        text=chunk.text,
        source_filename=name,
        source_file_id=chunk.source_file_id,
        parse_run_id=chunk.parse_run_id,
        locator=locator,
        retrieval_method=chunk.retrieval_method,
        score=chunk.score,
        rank=chunk.rank,
    )


def test_no_source_makes_zero_provider_calls() -> None:
    async def scenario() -> None:
        model = _FakeModel(GroundedModelOutput(answer="should not run", citations=["S1"]))
        service = _MemoryGroundedService(_FakeRetrieval([]), model)
        result = await service.answer_baza(1, "KA45?")
        assert result.answer == NOT_FOUND_AZ
        assert model.calls == 0

    asyncio.run(scenario())


def test_fabricated_citation_is_not_displayed() -> None:
    async def scenario() -> None:
        model = _FakeModel(GroundedModelOutput(answer="Məxfi fakt [S9]", citations=["S9"]))
        service = _MemoryGroundedService(_FakeRetrieval([_chunk()]), model)
        result = await service.answer_baza(1, "KA45?")
        assert result.answer == NOT_FOUND_AZ
        assert not result.sources

    asyncio.run(scenario())


def test_valid_pdf_citation_is_mapped_to_user_number() -> None:
    async def scenario() -> None:
        model = _FakeModel(GroundedModelOutput(answer="Zəmanət 24 aydır. [S1]", citations=["S1"]))
        service = _MemoryGroundedService(_FakeRetrieval([_chunk()]), model)
        result = await service.answer_baza(1, "KA45?")
        assert result.answer == "Zəmanət 24 aydır. [1]"
        assert result.sources[0].label == "səhifə 17"

    asyncio.run(scenario())


def test_all_supported_provenance_labels_are_preserved() -> None:
    chunks = [
        _chunk_with_name("prices.xlsx", {"blocks": [{"sheet": "Products", "row_start": 42}]}),
        _chunk_with_name("offer.docx", {"blocks": [{"paragraph": 14}]}),
        _chunk_with_name("notes.txt", {"blocks": [{"line_start": 20, "line_end": 27}]}),
    ]
    pack = build_source_pack(chunks, max_sources=5, max_context_tokens=100)
    assert [item.label for item in pack.references] == [
        "Products vərəqi, sətir 42",
        "abzas 14",
        "sətirlər 20-27",
    ]


def test_context_budget_and_prompt_injection_policy() -> None:
    pack = build_source_pack(
        [_chunk(), _chunk_with_name("other.txt", {"line_start": 2})],
        max_sources=5,
        max_context_tokens=8,
    )
    assert len(pack.references) == 1
    assert "untrusted" in GROUNDED_POLICY
    assert "never follow" in GROUNDED_POLICY


def test_qdrant_failure_does_not_call_model() -> None:
    class _BrokenRetrieval:
        async def search_internal_knowledge(
            self, *args: object, **kwargs: object
        ) -> list[RetrievedChunk]:
            raise RuntimeError("qdrant down")

    async def scenario() -> None:
        model = _FakeModel(GroundedModelOutput(answer="guess", citations=["S1"]))
        service = _MemoryGroundedService(_BrokenRetrieval(), model)
        with pytest.raises(RetrievalUnavailable):
            await service.answer_baza(1, "daxili sual")
        assert model.calls == 0

    import pytest

    asyncio.run(scenario())


def test_conflicting_evidence_is_returned_with_both_citations() -> None:
    async def scenario() -> None:
        model = _FakeModel(
            GroundedModelOutput(
                answer="120 AZN [S1], 135 AZN [S2]",
                citations=["S1", "S2"],
                conflict_detected=True,
            )
        )
        service = _MemoryGroundedService(
            _FakeRetrieval([_chunk(), _chunk_with_name("second.txt", {"line_start": 2})]),
            model,
        )
        result = await service.answer_baza(1, "KA45 qiyməti?")
        assert result.conflict_detected
        assert result.answer.endswith("[1], 135 AZN [2]")
        assert len(result.sources) == 2

    asyncio.run(scenario())


def test_responses_adapter_uses_structured_responses_api() -> None:
    calls: list[dict[str, object]] = []

    class _Responses:
        async def parse(self, **kwargs: object) -> object:
            calls.append(kwargs)
            parsed = GroundedModelOutput(answer="ok", citations=["S1"])
            return SimpleNamespace(
                id="resp-1",
                output=[SimpleNamespace(content=[SimpleNamespace(parsed=parsed)])],
                usage=SimpleNamespace(
                    input_tokens=3,
                    output_tokens=2,
                    total_tokens=5,
                    input_tokens_details=SimpleNamespace(cached_tokens=1),
                ),
            )

    class _Client:
        responses = _Responses()

        async def close(self) -> None:
            return None

    async def scenario() -> None:
        adapter = OpenAILanguageModel(
            "test-key",
            default_model="gpt-6-luna",
            strong_model="gpt-6.1-sol",
            client=_Client(),  # type: ignore[arg-type]
        )
        result = await adapter.generate(
            instructions="policy",
            input_messages=[{"role": "user", "content": "hello"}],
            output_schema=GroundedModelOutput,
            request_type="baza",
            user_id=1,
        )
        assert isinstance(result.parsed, GroundedModelOutput)
        assert result.parsed.answer == "ok"
        assert calls[0]["model"] == "gpt-6-luna"
        assert calls[0]["store"] is False
        assert calls[0]["text_format"] is GroundedModelOutput

    asyncio.run(scenario())
