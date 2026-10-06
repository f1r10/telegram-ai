"""Proposal metadata, stored-file links, and structured item services."""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_common import (
    require_actor,
    validate_decimal,
    validate_provenance,
)
from office_assistant.application.errors import EntityNotFound, InvalidBusinessValue
from office_assistant.business.normalization import normalize_currency
from office_assistant.business.types import ProposalItemInput, ProvenanceInput
from office_assistant.infrastructure.database.models import (
    FileObject,
    Proposal,
    ProposalDocument,
    ProposalDocumentRelation,
    ProposalItem,
    ProposalStatus,
)
from office_assistant.repositories.audit import AuditRepository
from office_assistant.repositories.customers import CustomerRepository
from office_assistant.repositories.products import ProductRepository
from office_assistant.repositories.proposals import ProposalRepository
from office_assistant.repositories.provenance import ProvenanceRepository


class ProposalService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_proposal(
        self,
        actor_telegram_user_id: int,
        *,
        customer_id: UUID,
        proposal_date: date,
        status: ProposalStatus,
        provenance: ProvenanceInput,
        proposal_number: str | None = None,
        notes: str | None = None,
        documents: Sequence[tuple[int, ProposalDocumentRelation]] = (),
        items: Sequence[ProposalItemInput] = (),
    ) -> Proposal:
        positions = [item.position for item in items]
        if len(positions) != len(set(positions)) or any(position <= 0 for position in positions):
            raise InvalidBusinessValue("Proposal item positions must be unique positive integers")
        async with self.session_factory.begin() as session:
            actor = await require_actor(session, actor_telegram_user_id)
            validate_provenance(provenance, actor)
            customer = await CustomerRepository(session).get(customer_id)
            if customer is None or not customer.is_active:
                raise EntityNotFound("Active customer does not exist")
            source = ProvenanceRepository(session).add(provenance, actor_user_id=actor.id)
            await session.flush()
            proposal = Proposal(
                customer_id=customer_id,
                proposal_number=proposal_number,
                proposal_date=proposal_date,
                status=status,
                notes=notes,
                source_reference_id=source.id,
                created_by_user_id=actor.id,
                is_active=True,
            )
            repo = ProposalRepository(session)
            repo.add(proposal)
            await session.flush()
            for file_id, relation in documents:
                if await session.get(FileObject, file_id) is None:
                    raise EntityNotFound("Proposal document file does not exist")
                repo.add_document(
                    ProposalDocument(
                        proposal_id=proposal.id, file_id=file_id, relation_type=relation
                    )
                )
            for value in items:
                validate_provenance(value.provenance, actor)
                await self._add_item(session, repo, actor.id, proposal.id, value)
            await session.flush()
            AuditRepository(session).add(
                actor_user_id=actor.id,
                action="PROPOSAL_CREATED",
                entity_type="proposal",
                entity_id=str(proposal.id),
                metadata={"item_count": len(items), "document_count": len(documents)},
            )
            return proposal

    async def _add_item(
        self,
        session: AsyncSession,
        repo: ProposalRepository,
        actor_user_id: int,
        proposal_id: UUID,
        value: ProposalItemInput,
    ) -> None:
        if value.product_id is None and not (value.raw_product_name or "").strip():
            raise InvalidBusinessValue("Proposal item needs a product or raw product name")
        if (
            value.product_id is not None
            and await ProductRepository(session).get(value.product_id) is None
        ):
            raise EntityNotFound("Proposal item product does not exist")
        for field, amount, allow_zero in (
            ("quantity", value.quantity, False),
            ("unit_price", value.unit_price, True),
            ("line_total", value.line_total, True),
        ):
            if amount is not None:
                validate_decimal(amount, field=field, allow_zero=allow_zero)
        currency = normalize_currency(value.currency) if value.currency is not None else None
        item_source = ProvenanceRepository(session).add(
            value.provenance, actor_user_id=actor_user_id
        )
        await session.flush()
        repo.add_item(
            ProposalItem(
                proposal_id=proposal_id,
                product_id=value.product_id,
                raw_product_name=(value.raw_product_name or "").strip() or None,
                quantity=value.quantity,
                unit_price=value.unit_price,
                currency=currency,
                line_total=value.line_total,
                source_reference_id=item_source.id,
                position=value.position,
            )
        )

    async def get_latest_customer_proposal(self, customer_id: UUID) -> Proposal | None:
        async with self.session_factory() as session:
            return await ProposalRepository(session).latest_for_customer(customer_id)

    async def get_customer_proposals(self, customer_id: UUID) -> list[Proposal]:
        async with self.session_factory() as session:
            return await ProposalRepository(session).list_for_customer(customer_id)

    async def get_proposal_items(self, proposal_id: UUID) -> list[ProposalItem]:
        async with self.session_factory() as session:
            return await ProposalRepository(session).items(proposal_id)

    async def deactivate_proposal(self, actor_telegram_user_id: int, proposal_id: UUID) -> Proposal:
        async with self.session_factory.begin() as session:
            actor = await require_actor(session, actor_telegram_user_id, admin=True)
            proposal = await session.get(Proposal, proposal_id, with_for_update=True)
            if proposal is None:
                raise EntityNotFound("Proposal does not exist")
            if proposal.is_active:
                proposal.is_active = False
                proposal.deactivated_at = datetime.now(UTC)
                proposal.deactivated_by_user_id = actor.id
                AuditRepository(session).add(
                    actor_user_id=actor.id,
                    action="PROPOSAL_DEACTIVATED",
                    entity_type="proposal",
                    entity_id=str(proposal.id),
                )
            return proposal
