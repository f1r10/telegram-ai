"""Bounded concurrent Telegram updates with sequential execution per user.

The bot is stateful: auth, menu routing, document drafts and approvals must never
race within one user's updates. At the same time, a slow retrieval or AI call
must not block unrelated office workers.
"""

import asyncio
from collections.abc import Awaitable
from weakref import WeakValueDictionary

from telegram import Update
from telegram.ext import BaseUpdateProcessor


class UserSerialUpdateProcessor(BaseUpdateProcessor):
    """Run independent users concurrently but serialize each user's updates.

    BaseUpdateProcessor supplies the global concurrency semaphore. Weakly held
    locks prevent an indefinitely growing per-user lock registry.
    """

    def __init__(self, max_concurrent_updates: int = 8) -> None:
        super().__init__(max_concurrent_updates)
        self._user_locks: WeakValueDictionary[tuple[str, int], asyncio.Lock] = (
            WeakValueDictionary()
        )

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        self._user_locks.clear()

    @staticmethod
    def _key(update: object) -> tuple[str, int]:
        if isinstance(update, Update):
            user = update.effective_user
            if user is not None:
                return ("user", user.id)
            chat = update.effective_chat
            if chat is not None:
                return ("chat", chat.id)
        # Unknown updates have no safely resolved identity: serialize them.
        return ("unidentified", 0)

    async def do_process_update(self, update: object, coroutine: Awaitable[object]) -> None:
        key = self._key(update)
        lock = self._user_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[key] = lock
        async with lock:
            await coroutine
