"""Lightweight customer business-event recording and queries."""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_common import require_actor, validate_provenance
from office_assistant.application.errors import EntityNotFound, InvalidBusinessValue
from office_assistant.business.types import ProvenanceInput
from office_assistant.infrastructure.database.models import BusinessEvent, BusinessEventType
from office_assistant.repositories.audit import AuditRepository
from office_assistant.repositories.customers import CustomerRepository
from office_assistant.repositories.events import BusinessEventRepository
from office_assistant.repositories.provenance import ProvenanceRepository


class BusinessEventService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_event(
        self,
        actor_telegram_user_id: int,
        *,
        customer_id: UUID,
        event_type: BusinessEventType,
        occurred_at: datetime,
        summary: str,
        provenance: ProvenanceInput,
        details: str | None = None,
    ) -> BusinessEvent:
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise InvalidBusinessValue("occurred_at must be timezone-aware")
        if not summary.strip():
            raise InvalidBusinessValue("summary must not be empty")
        async with self.session_factory.begin() as session:
            actor = await require_actor(session, actor_telegram_user_id)
            validate_provenance(provenance, actor)
            customer = await CustomerRepository(session).get(customer_id)
            if customer is None or not customer.is_active:
                raise EntityNotFound("Active customer does not exist")
            source = ProvenanceRepository(session).add(provenance, actor_user_id=actor.id)
            await session.flush()
            event = BusinessEvent(
                customer_id=customer_id,
                event_type=event_type,
                occurred_at=occurred_at,
                summary=summary.strip(),
                details=details,
                source_reference_id=source.id,
                created_by_user_id=actor.id,
            )
            BusinessEventRepository(session).add(event)
            await session.flush()
            AuditRepository(session).add(
                actor_user_id=actor.id,
                action="BUSINESS_EVENT_CREATED",
                entity_type="business_event",
                entity_id=str(event.id),
            )
            return event

    async def get_customer_events(
        self,
        customer_id: UUID,
        *,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> list[BusinessEvent]:
        async with self.session_factory() as session:
            return await BusinessEventRepository(session).list_for_customer(
                customer_id=customer_id, from_date=from_date, to_date=to_date
            )
