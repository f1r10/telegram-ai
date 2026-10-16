"""Telegram throughput: distinct workers parallel, one worker's state sequential."""

import asyncio
from datetime import UTC, datetime

from telegram import Chat, Message, Update, User
from telegram.constants import ChatType

from office_assistant.integrations.telegram.app import create_telegram_application
from office_assistant.integrations.telegram.update_processor import UserSerialUpdateProcessor


def _update(user_id: int, update_id: int) -> Update:
    user = User(id=user_id, first_name="Office User", is_bot=False)
    message = Message(
        message_id=update_id,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type=ChatType.PRIVATE),
        from_user=user,
    )
    return Update(update_id=update_id, message=message)


def test_separate_users_progress_while_first_user_is_waiting() -> None:
    async def exercise() -> None:
        first_started = asyncio.Event()
        first_release = asyncio.Event()
        second_started = asyncio.Event()
        independent_started = asyncio.Event()
        order: list[str] = []

        async def first() -> None:
            order.append("first-start")
            first_started.set()
            await first_release.wait()
            order.append("first-end")

        async def same_user() -> None:
            order.append("second-start")
            second_started.set()

        async def other_user() -> None:
            order.append("other-start")
            independent_started.set()

        async with UserSerialUpdateProcessor(8) as processor:
            first_task = asyncio.create_task(processor.process_update(_update(123, 1), first()))
            await asyncio.wait_for(first_started.wait(), timeout=2)
            second_task = asyncio.create_task(
                processor.process_update(_update(123, 2), same_user())
            )
            third_task = asyncio.create_task(
                processor.process_update(_update(124, 3), other_user())
            )
            await asyncio.wait_for(independent_started.wait(), timeout=2)
            assert not second_started.is_set()
            assert order == ["first-start", "other-start"]
            first_release.set()
            await asyncio.wait_for(
                asyncio.gather(first_task, second_task, third_task), timeout=2
            )
            assert order == ["first-start", "other-start", "first-end", "second-start"]

    asyncio.run(exercise())


def test_application_uses_bounded_user_serial_processor() -> None:
    # No network calls are required to inspect the Telegram application builder.
    from typing import cast
    from unittest.mock import MagicMock

    from office_assistant.integrations.telegram.handlers import TelegramServices

    application = create_telegram_application(
        "test-token", cast(TelegramServices, MagicMock())
    )
    assert isinstance(application.update_processor, UserSerialUpdateProcessor)
    assert application.update_processor.max_concurrent_updates == 8
