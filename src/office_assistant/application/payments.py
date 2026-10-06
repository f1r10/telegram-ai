"""Payment recording, history, grouped totals, and administrative deactivation."""

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
from office_assistant.business.types import ProvenanceInput
from office_assistant.infrastructure.database.models import Payment
from office_assistant.repositories.audit import AuditRepository
from office_assistant.repositories.customers import CustomerRepository
from office_assistant.repositories.payments import PaymentRepository
from office_assistant.repositories.provenance import ProvenanceRepository


class PaymentService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_payment(
        self,
        actor_telegram_user_id: int,
        *,
        customer_id: UUID,
        amount: Decimal,
        currency: str,
        paid_at: datetime,
        provenance: ProvenanceInput,
        description: str | None = None,
    ) -> Payment:
        validate_decimal(amount, field="amount", allow_zero=False)
        if paid_at.tzinfo is None or paid_at.utcoffset() is None:
            raise InvalidBusinessValue("paid_at must be timezone-aware")
        normalized_currency = normalize_currency(currency)
        async with self.session_factory.begin() as session:
            actor = await require_actor(session, actor_telegram_user_id)
            validate_provenance(provenance, actor)
            customer = await CustomerRepository(session).get(customer_id)
            if customer is None or not customer.is_active:
                raise EntityNotFound("Active customer does not exist")
            source = ProvenanceRepository(session).add(provenance, actor_user_id=actor.id)
            await session.flush()
            payment = Payment(
                customer_id=customer_id,
                amount=amount,
                currency=normalized_currency,
                paid_at=paid_at,
                description=description,
                source_reference_id=source.id,
                created_by_user_id=actor.id,
                is_active=True,
            )
            PaymentRepository(session).add(payment)
            await session.flush()
            AuditRepository(session).add(
                actor_user_id=actor.id,
                action="PAYMENT_CREATED",
                entity_type="payment",
                entity_id=str(payment.id),
            )
            return payment

    async def get_customer_payments(
        self,
        customer_id: UUID,
        *,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> list[Payment]:
        async with self.session_factory() as session:
            return await PaymentRepository(session).history(
                customer_id=customer_id, from_date=from_date, to_date=to_date
            )

    async def get_customer_payment_total(
        self,
        customer_id: UUID,
        *,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> dict[str, Decimal]:
        async with self.session_factory() as session:
            return await PaymentRepository(session).totals(
                customer_id=customer_id, from_date=from_date, to_date=to_date
            )

    async def deactivate_payment(self, actor_telegram_user_id: int, payment_id: UUID) -> Payment:
        async with self.session_factory.begin() as session:
            actor = await require_actor(session, actor_telegram_user_id, admin=True)
            payment = await session.get(Payment, payment_id, with_for_update=True)
            if payment is None:
                raise EntityNotFound("Payment does not exist")
            if payment.is_active:
                payment.is_active = False
                payment.deactivated_at = datetime.now(UTC)
                payment.deactivated_by_user_id = actor.id
                AuditRepository(session).add(
                    actor_user_id=actor.id,
                    action="PAYMENT_DEACTIVATED",
                    entity_type="payment",
                    entity_id=str(payment.id),
                )
            return payment
