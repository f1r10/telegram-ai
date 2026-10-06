"""Customer payment persistence and aggregate queries."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import Payment


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, payment: Payment) -> Payment:
        self.session.add(payment)
        return payment

    async def history(
        self,
        *,
        customer_id: UUID,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[Payment]:
        statement = select(Payment).where(
            Payment.customer_id == customer_id, Payment.is_active.is_(True)
        )
        if from_date is not None:
            statement = statement.where(Payment.paid_at >= from_date)
        if to_date is not None:
            statement = statement.where(Payment.paid_at <= to_date)
        statement = statement.order_by(Payment.paid_at, Payment.id)
        return list((await self.session.execute(statement)).scalars().all())

    async def totals(
        self,
        *,
        customer_id: UUID,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> dict[str, Decimal]:
        statement = select(Payment.currency, func.sum(Payment.amount)).where(
            Payment.customer_id == customer_id, Payment.is_active.is_(True)
        )
        if from_date is not None:
            statement = statement.where(Payment.paid_at >= from_date)
        if to_date is not None:
            statement = statement.where(Payment.paid_at <= to_date)
        rows = (await self.session.execute(statement.group_by(Payment.currency))).all()
        return {currency: total for currency, total in rows}
