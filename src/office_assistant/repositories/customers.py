"""Customer and customer-alias persistence queries."""

from uuid import UUID

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import Customer, CustomerAlias


class CustomerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_identity_namespace(self) -> None:
        await self.session.execute(text("SELECT pg_advisory_xact_lock(71002)"))

    async def get(self, customer_id: UUID, *, for_update: bool = False) -> Customer | None:
        statement: Select[tuple[Customer]] = select(Customer).where(Customer.id == customer_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def find_by_name(
        self, normalized_name: str, *, active_only: bool = True
    ) -> Customer | None:
        statement = select(Customer).where(Customer.normalized_name == normalized_name)
        if active_only:
            statement = statement.where(Customer.is_active.is_(True))
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def find_by_alias(
        self, normalized_alias: str, *, active_only: bool = True
    ) -> Customer | None:
        statement = (
            select(Customer)
            .join(CustomerAlias, CustomerAlias.customer_id == Customer.id)
            .where(CustomerAlias.normalized_alias == normalized_alias)
        )
        if active_only:
            statement = statement.where(Customer.is_active.is_(True))
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def identity_conflict(
        self, normalized_value: str, *, exclude_id: UUID | None = None
    ) -> bool:
        customers = select(Customer.id).where(Customer.normalized_name == normalized_value)
        aliases = select(CustomerAlias.customer_id).where(
            CustomerAlias.normalized_alias == normalized_value
        )
        if exclude_id is not None:
            customers = customers.where(Customer.id != exclude_id)
            aliases = aliases.where(CustomerAlias.customer_id != exclude_id)
        return (
            await self.session.execute(customers.union_all(aliases).limit(1))
        ).first() is not None

    def add(
        self,
        *,
        display_name: str,
        normalized_name: str,
        legal_name: str | None,
        notes: str | None,
        actor_user_id: int,
        source_reference_id: UUID,
    ) -> Customer:
        customer = Customer(
            display_name=display_name,
            normalized_name=normalized_name,
            legal_name=legal_name,
            notes=notes,
            is_active=True,
            created_by_user_id=actor_user_id,
            source_reference_id=source_reference_id,
        )
        self.session.add(customer)
        return customer

    def add_alias(
        self,
        *,
        customer_id: UUID,
        alias: str,
        normalized_alias: str,
        actor_user_id: int,
        source_reference_id: UUID,
    ) -> CustomerAlias:
        item = CustomerAlias(
            customer_id=customer_id,
            alias=alias,
            normalized_alias=normalized_alias,
            created_by_user_id=actor_user_id,
            source_reference_id=source_reference_id,
        )
        self.session.add(item)
        return item
