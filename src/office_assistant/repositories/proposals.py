"""Proposal, document-link, and item persistence queries."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import (
    Proposal,
    ProposalDocument,
    ProposalItem,
)


class ProposalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, proposal: Proposal) -> Proposal:
        self.session.add(proposal)
        return proposal

    def add_document(self, item: ProposalDocument) -> ProposalDocument:
        self.session.add(item)
        return item

    def add_item(self, item: ProposalItem) -> ProposalItem:
        self.session.add(item)
        return item

    async def latest_for_customer(self, customer_id: UUID) -> Proposal | None:
        statement = (
            select(Proposal)
            .where(Proposal.customer_id == customer_id, Proposal.is_active.is_(True))
            .order_by(Proposal.proposal_date.desc(), Proposal.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_for_customer(self, customer_id: UUID) -> list[Proposal]:
        statement = (
            select(Proposal)
            .where(Proposal.customer_id == customer_id, Proposal.is_active.is_(True))
            .order_by(Proposal.proposal_date.desc(), Proposal.created_at.desc())
        )
        return list((await self.session.execute(statement)).scalars().all())

    async def items(self, proposal_id: UUID) -> list[ProposalItem]:
        statement = (
            select(ProposalItem)
            .where(ProposalItem.proposal_id == proposal_id)
            .order_by(ProposalItem.position)
        )
        return list((await self.session.execute(statement)).scalars().all())
