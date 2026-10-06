"""Customer identity, aliases, lookup, and administrative deactivation."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_common import require_actor, validate_provenance
from office_assistant.application.errors import DomainConflict, EntityNotFound
from office_assistant.business.normalization import (
    normalize_customer_alias,
    normalize_customer_name,
)
from office_assistant.business.types import ProvenanceInput
from office_assistant.infrastructure.database.models import Customer, CustomerAlias, SourceType
from office_assistant.repositories.audit import AuditRepository
from office_assistant.repositories.customers import CustomerRepository
from office_assistant.repositories.provenance import ProvenanceRepository


class CustomerService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_customer(
        self,
        actor_telegram_user_id: int,
        *,
        display_name: str,
        legal_name: str | None = None,
        notes: str | None = None,
        provenance: ProvenanceInput | None = None,
    ) -> Customer:
        normalized = normalize_customer_name(display_name)
        try:
            async with self.session_factory.begin() as session:
                actor = await require_actor(session, actor_telegram_user_id)
                source_value = provenance or ProvenanceInput(SourceType.TELEGRAM_USER)
                validate_provenance(source_value, actor)
                source = ProvenanceRepository(session).add(source_value, actor_user_id=actor.id)
                await session.flush()
                repo = CustomerRepository(session)
                await repo.lock_identity_namespace()
                if await repo.identity_conflict(normalized):
                    raise DomainConflict("Customer identity is already owned")
                customer = repo.add(
                    display_name=display_name.strip(),
                    normalized_name=normalized,
                    legal_name=legal_name,
                    notes=notes,
                    actor_user_id=actor.id,
                    source_reference_id=source.id,
                )
                await session.flush()
                AuditRepository(session).add(
                    actor_user_id=actor.id,
                    action="CUSTOMER_CREATED",
                    entity_type="customer",
                    entity_id=str(customer.id),
                )
                return customer
        except IntegrityError as exc:
            raise DomainConflict("Customer identity already exists") from exc

    async def add_alias(
        self,
        actor_telegram_user_id: int,
        customer_id: UUID,
        alias: str,
        provenance: ProvenanceInput | None = None,
    ) -> CustomerAlias:
        normalized = normalize_customer_alias(alias)
        try:
            async with self.session_factory.begin() as session:
                actor = await require_actor(session, actor_telegram_user_id)
                source_value = provenance or ProvenanceInput(SourceType.TELEGRAM_USER)
                validate_provenance(source_value, actor)
                source = ProvenanceRepository(session).add(source_value, actor_user_id=actor.id)
                await session.flush()
                repo = CustomerRepository(session)
                await repo.lock_identity_namespace()
                customer = await repo.get(customer_id)
                if customer is None:
                    raise EntityNotFound("Customer does not exist")
                if await repo.identity_conflict(normalized, exclude_id=customer_id):
                    raise DomainConflict("Customer alias is already owned")
                item = repo.add_alias(
                    customer_id=customer_id,
                    alias=alias.strip(),
                    normalized_alias=normalized,
                    actor_user_id=actor.id,
                    source_reference_id=source.id,
                )
                await session.flush()
                AuditRepository(session).add(
                    actor_user_id=actor.id,
                    action="CUSTOMER_ALIAS_ADDED",
                    entity_type="customer_alias",
                    entity_id=str(item.id),
                    metadata={"customer_id": str(customer_id)},
                )
                return item
        except IntegrityError as exc:
            raise DomainConflict("Customer alias already exists") from exc

    async def find_customer_by_name(self, value: str) -> Customer | None:
        async with self.session_factory() as session:
            return await CustomerRepository(session).find_by_name(normalize_customer_name(value))

    async def search_customer_by_alias(self, value: str) -> Customer | None:
        async with self.session_factory() as session:
            return await CustomerRepository(session).find_by_alias(normalize_customer_alias(value))

    async def get_customer(self, customer_id: UUID) -> Customer | None:
        async with self.session_factory() as session:
            return await CustomerRepository(session).get(customer_id)

    async def deactivate_customer(self, actor_telegram_user_id: int, customer_id: UUID) -> Customer:
        async with self.session_factory.begin() as session:
            actor = await require_actor(session, actor_telegram_user_id, admin=True)
            customer = await CustomerRepository(session).get(customer_id, for_update=True)
            if customer is None:
                raise EntityNotFound("Customer does not exist")
            if customer.is_active:
                customer.is_active = False
                customer.deactivated_at = datetime.now(UTC)
                customer.deactivated_by_user_id = actor.id
                AuditRepository(session).add(
                    actor_user_id=actor.id,
                    action="CUSTOMER_DEACTIVATED",
                    entity_type="customer",
                    entity_id=str(customer.id),
                )
            return customer
