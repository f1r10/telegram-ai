"""Authoritative price history queries."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import PriceKind, ProductPrice


class PriceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, price: ProductPrice) -> ProductPrice:
        self.session.add(price)
        return price

    async def get(self, price_id: UUID, *, for_update: bool = False) -> ProductPrice | None:
        statement: Select[tuple[ProductPrice]] = select(ProductPrice).where(
            ProductPrice.id == price_id
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def applicable(
        self, *, product_id: UUID, customer_id: UUID | None, at: datetime
    ) -> list[ProductPrice]:
        kind = PriceKind.CUSTOMER_SPECIFIC if customer_id is not None else PriceKind.GENERAL
        statement = select(ProductPrice).where(
            ProductPrice.product_id == product_id,
            ProductPrice.price_kind == kind,
            ProductPrice.effective_at <= at,
            (ProductPrice.valid_until.is_(None) | (ProductPrice.valid_until > at)),
            ProductPrice.superseded_at.is_(None),
        )
        if customer_id is None:
            statement = statement.where(ProductPrice.customer_id.is_(None))
        else:
            statement = statement.where(ProductPrice.customer_id == customer_id)
        statement = statement.order_by(ProductPrice.effective_at.desc(), ProductPrice.id)
        return list((await self.session.execute(statement)).scalars().all())

    async def history(
        self,
        *,
        product_id: UUID,
        customer_id: UUID | None,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[ProductPrice]:
        statement = select(ProductPrice).where(ProductPrice.product_id == product_id)
        if customer_id is None:
            statement = statement.where(ProductPrice.customer_id.is_(None))
        else:
            statement = statement.where(ProductPrice.customer_id == customer_id)
        if from_date is not None:
            statement = statement.where(ProductPrice.effective_at >= from_date)
        if to_date is not None:
            statement = statement.where(ProductPrice.effective_at <= to_date)
        statement = statement.order_by(ProductPrice.effective_at, ProductPrice.created_at)
        return list((await self.session.execute(statement)).scalars().all())
