"""Customer business-event queries."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import BusinessEvent


class BusinessEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, event: BusinessEvent) -> BusinessEvent:
        self.session.add(event)
        return event

    async def list_for_customer(
        self,
        *,
        customer_id: UUID,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[BusinessEvent]:
        statement = select(BusinessEvent).where(BusinessEvent.customer_id == customer_id)
        if from_date is not None:
            statement = statement.where(BusinessEvent.occurred_at >= from_date)
        if to_date is not None:
            statement = statement.where(BusinessEvent.occurred_at <= to_date)
        statement = statement.order_by(BusinessEvent.occurred_at.desc(), BusinessEvent.id)
        return list((await self.session.execute(statement)).scalars().all())
