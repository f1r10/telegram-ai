import asyncio
from typing import cast

import pytest
from sqlalchemy import delete, func, select

from office_assistant.ai.provider import ModelResponse, TokenUsage
from office_assistant.ai.schemas import GeneralModelOutput, GroundedModelOutput
from office_assistant.ai.service import NOT_FOUND_AZ, GroundedAnswerService
from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import (
    AIConversation,
    AIMessage,
    AIUsage,
    DocumentParseRun,
    FileObject,
    IngestionJob,
    IngestionSession,
    IngestionSessionItem,
    KnowledgeIndexJob,
    OutboxEvent,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.infrastructure.database.session import create_session_factory

pytestmark = pytest.mark.integration


class _FakeRetrieval:
    async def search_internal_knowledge(self, *args: object, **kwargs: object) -> list[object]:
        return []


class _FakeModel:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    async def generate(self, **kwargs: object) -> ModelResponse:
        messages = cast(list[dict[str, str]], kwargs["input_messages"])
        self.calls.append(messages)
        schema = kwargs["output_schema"]
        if schema is GeneralModelOutput:
            output = GeneralModelOutput(answer="Ümumi cavab")
        else:
            output = GroundedModelOutput(answer="Daxili cavab", citations=[])
        return ModelResponse(output, "test-model", None, TokenUsage(1, 0, 1, 2), 1)


def test_conversation_history_is_bounded_and_user_scoped() -> None:
    settings = load_settings()
    if not settings.postgres_password or not settings.postgres_host:
        pytest.skip("PostgreSQL integration settings are not configured")

    async def scenario() -> None:
        engine = create_database_engine(settings)
        factory = create_session_factory(engine)
        try:
            async with factory.begin() as session:
                await session.execute(delete(AIUsage))
                await session.execute(delete(AIMessage))
                await session.execute(delete(AIConversation))
                await session.execute(delete(OutboxEvent))
                await session.execute(delete(KnowledgeIndexJob))
                await session.execute(delete(DocumentParseRun))
                await session.execute(delete(IngestionJob))
                await session.execute(delete(IngestionSessionItem))
                await session.execute(delete(IngestionSession))
                await session.execute(delete(FileObject))
                await session.execute(delete(User))
                first = User(telegram_user_id=960001, role=UserRole.USER, status=UserStatus.ACTIVE)
                second = User(telegram_user_id=960002, role=UserRole.USER, status=UserStatus.ACTIVE)
                session.add_all([first, second])
                await session.flush()
                first_id, second_id = first.id, second.id

            model = _FakeModel()
            service = GroundedAnswerService(
                factory,
                _FakeRetrieval(),  # type: ignore[arg-type]
                model,  # type: ignore[arg-type]
                history_max_messages=2,
                history_max_tokens=100,
            )
            await service.answer_general(first_id, "Birinci sual")
            await service.answer_general(first_id, "İkinci sual")
            await service.answer_general(second_id, "Başqa istifadəçi")
            assert len(model.calls) == 3
            assert len(model.calls[1]) == 3
            assert all("Birinci sual" not in item["content"] for item in model.calls[2])
            no_source = await service.answer_baza(first_id, "Daxili fakt")
            assert no_source.answer == NOT_FOUND_AZ
            assert len(model.calls) == 3

            async with factory() as session:
                usage_count = await session.scalar(select(func.count()).select_from(AIUsage))
                assert usage_count == 3
        finally:
            async with factory.begin() as session:
                await session.execute(delete(AIUsage))
                await session.execute(delete(AIMessage))
                await session.execute(delete(AIConversation))
                await session.execute(delete(OutboxEvent))
                await session.execute(delete(KnowledgeIndexJob))
                await session.execute(delete(DocumentParseRun))
                await session.execute(delete(IngestionJob))
                await session.execute(delete(IngestionSessionItem))
                await session.execute(delete(IngestionSession))
                await session.execute(delete(FileObject))
                await session.execute(delete(User))
            await engine.dispose()

    asyncio.run(scenario())
