"""Developer-only deterministic harness for the Phase 9 business tool registry."""

import argparse
import asyncio
import json
from typing import cast

from sqlalchemy import select

from office_assistant.business_tools.models import ToolExecutionContext
from office_assistant.business_tools.registry import BusinessToolRegistry
from office_assistant.business_tools.service import BusinessQueryService
from office_assistant.core.settings import load_settings
from office_assistant.infrastructure.database.engine import create_database_engine
from office_assistant.infrastructure.database.models import User
from office_assistant.infrastructure.database.session import create_session_factory


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schemas", action="store_true", help="Print adapter-neutral schemas")
    parser.add_argument("--tool", help="Allowlisted tool name")
    parser.add_argument("--payload", default="{}", help="JSON object for the typed input")
    parser.add_argument("--actor-telegram-id", type=int, help="Authenticated test actor")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    settings = load_settings()
    engine = create_database_engine(settings)
    factory = create_session_factory(engine)
    registry = BusinessToolRegistry(BusinessQueryService(factory))
    try:
        if args.schemas:
            print(json.dumps(registry.schemas(), ensure_ascii=False, indent=2))
            return
        if not args.tool or args.actor_telegram_id is None:
            raise SystemExit("--tool and --actor-telegram-id are required unless --schemas is used")
        payload = json.loads(args.payload)
        if not isinstance(payload, dict):
            raise SystemExit("--payload must be a JSON object")
        async with factory() as session:
            actor = await session.scalar(
                select(User).where(User.telegram_user_id == args.actor_telegram_id)
            )
        if actor is None:
            raise SystemExit("Actor does not exist")
        context = ToolExecutionContext.model_validate(
            {
                "user_id": actor.id,
                "role": str(actor.role),
                "request_id": "developer-harness",
            }
        )
        result = await registry.execute(args.tool, context, cast(dict[str, object], payload))
        print(result.model_dump_json(indent=2))
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run(_arguments()))


if __name__ == "__main__":
    main()
