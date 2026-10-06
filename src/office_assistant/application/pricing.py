"""Historical pricing mutations and deterministic price resolution."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_common import (
    require_actor,
    validate_decimal,
    validate_provenance,
)
from office_assistant.application.errors import EntityNotFound, InvalidBusinessValue
from office_assistant.business.normalization import normalize_currency
from office_assistant.business.types import (
    PriceResolutionStatus,
    ProvenanceInput,
    ResolvedPrice,
)
from office_assistant.infrastructure.database.models import PriceKind, ProductPrice
from office_assistant.repositories.audit import AuditRepository
from office_assistant.repositories.customers import CustomerRepository
from office_assistant.repositories.pricing import PriceRepository
from office_assistant.repositories.products import ProductRepository
from office_assistant.repositories.provenance import ProvenanceRepository


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidBusinessValue(f"{field} must be timezone-aware")


class PricingService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def add_price(
        self,
        actor_telegram_user_id: int,
        *,
        product_id: UUID,
        amount: Decimal,
        currency: str,
        effective_at: datetime,
        provenance: ProvenanceInput,
        customer_id: UUID | None = None,
        valid_until: datetime | None = None,
    ) -> ProductPrice:
        validate_decimal(amount, field="amount", allow_zero=True)
        _aware(effective_at, "effective_at")
        if valid_until is not None:
            _aware(valid_until, "valid_until")
            if valid_until <= effective_at:
                raise InvalidBusinessValue("valid_until must be after effective_at")
        normalized_currency = normalize_currency(currency)
        async with self.session_factory.begin() as session:
            actor = await require_actor(session, actor_telegram_user_id)
            validate_provenance(provenance, actor)
            product = await ProductRepository(session).get(product_id)
            if product is None or not product.is_active:
                raise EntityNotFound("Active product does not exist")
            if customer_id is not None:
                customer = await CustomerRepository(session).get(customer_id)
                if customer is None or not customer.is_active:
                    raise EntityNotFound("Active customer does not exist")
            source = ProvenanceRepository(session).add(provenance, actor_user_id=actor.id)
            await session.flush()
            price = ProductPrice(
                product_id=product_id,
                customer_id=customer_id,
                amount=amount,
                currency=normalized_currency,
                price_kind=(
                    PriceKind.CUSTOMER_SPECIFIC if customer_id is not None else PriceKind.GENERAL
                ),
                effective_at=effective_at,
                valid_until=valid_until,
                source_reference_id=source.id,
                created_by_user_id=actor.id,
            )
            PriceRepository(session).add(price)
            await session.flush()
            AuditRepository(session).add(
                actor_user_id=actor.id,
                action="PRICE_ADDED",
                entity_type="product_price",
                entity_id=str(price.id),
                metadata={"product_id": str(product_id), "customer_id": str(customer_id)},
            )
            return price

    async def resolve_current_price(
        self,
        product_id: UUID,
        *,
        customer_id: UUID | None = None,
        at: datetime | None = None,
    ) -> ResolvedPrice:
        instant = at or datetime.now(UTC)
        _aware(instant, "at")
        async with self.session_factory() as session:
            candidates = await PriceRepository(session).applicable(
                product_id=product_id, customer_id=customer_id, at=instant
            )
            if not candidates:
                return ResolvedPrice(status=PriceResolutionStatus.NOT_FOUND)
            latest_at = candidates[0].effective_at
            latest = tuple(item for item in candidates if item.effective_at == latest_at)
            if len(latest) != 1:
                return ResolvedPrice(
                    status=PriceResolutionStatus.CONFLICT, conflicting_prices=latest
                )
            return ResolvedPrice(status=PriceResolutionStatus.FOUND, price=latest[0])

    async def get_price_history(
        self,
        product_id: UUID,
        *,
        customer_id: UUID | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> list[ProductPrice]:
        async with self.session_factory() as session:
            return await PriceRepository(session).history(
                product_id=product_id,
                customer_id=customer_id,
                from_date=from_date,
                to_date=to_date,
            )

    async def supersede_price(
        self, actor_telegram_user_id: int, old_price_id: UUID, new_price_id: UUID
    ) -> ProductPrice:
        if old_price_id == new_price_id:
            raise InvalidBusinessValue("A price cannot supersede itself")
        async with self.session_factory.begin() as session:
            actor = await require_actor(session, actor_telegram_user_id, admin=True)
            repo = PriceRepository(session)
            old = await repo.get(old_price_id, for_update=True)
            new = await repo.get(new_price_id, for_update=True)
            if old is None or new is None:
                raise EntityNotFound("Price does not exist")
            if (old.product_id, old.customer_id, old.price_kind) != (
                new.product_id,
                new.customer_id,
                new.price_kind,
            ):
                raise InvalidBusinessValue("Superseding price must have the same scope")
            if new.superseded_at is not None:
                raise InvalidBusinessValue("A superseded price cannot replace another price")
            if old.superseded_at is not None:
                if old.superseded_by_id == new.id:
                    return old
                raise InvalidBusinessValue("Price has already been superseded")
            old.superseded_at = datetime.now(UTC)
            old.superseded_by_id = new.id
            AuditRepository(session).add(
                actor_user_id=actor.id,
                action="PRICE_SUPERSEDED",
                entity_type="product_price",
                entity_id=str(old.id),
                metadata={"superseded_by_id": str(new.id)},
            )
            return old
