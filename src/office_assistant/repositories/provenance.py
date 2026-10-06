"""Repository for bounded business provenance."""

from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.business.types import ProvenanceInput
from office_assistant.infrastructure.database.models import SourceReference


class ProvenanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, value: ProvenanceInput, *, actor_user_id: int) -> SourceReference:
        source = SourceReference(
            source_type=value.source_type,
            file_id=value.file_id,
            ingestion_session_id=value.ingestion_session_id,
            ingestion_session_item_id=value.ingestion_session_item_id,
            document_parse_run_id=value.document_parse_run_id,
            actor_user_id=actor_user_id,
            locator=value.locator,
            description=value.description,
        )
        self.session.add(source)
        return source
