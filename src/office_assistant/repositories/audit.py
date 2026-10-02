"""Repository for audit events."""

from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import AuditEvent


class AuditRepository:
    """Creates audit events in the caller's transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(
        self,
        *,
        actor_user_id: int | None,
        action: str,
        entity_type: str,
        entity_id: str,
        metadata: dict[str, object] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            event_metadata=metadata,
        )
        self.session.add(event)
        return event
