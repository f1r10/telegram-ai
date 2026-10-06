"""Authorized, idempotent review and Phase 7 service application."""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_common import require_actor
from office_assistant.application.business_events import BusinessEventService
from office_assistant.application.customers import CustomerService
from office_assistant.application.errors import AuthorizationDenied, DomainConflict
from office_assistant.application.payments import PaymentService
from office_assistant.application.pricing import PricingService
from office_assistant.application.products import ProductService
from office_assistant.application.proposals import ProposalService
from office_assistant.auth.policy import is_admin
from office_assistant.business.types import ProposalItemInput, ProvenanceInput
from office_assistant.extraction.service import review_summary
from office_assistant.extraction.sources import ExtractionSource, load_extraction_sources
from office_assistant.infrastructure.database.models import (
    AuditEvent,
    BusinessEventType,
    CandidateStatus,
    ExtractionJobStatus,
    ExtractionReviewStatus,
    ProposalDocumentRelation,
    ProposalStatus,
    SourceType,
    StructuredExtractionCandidate,
    StructuredExtractionJob,
    StructuredExtractionRun,
    User,
)
from office_assistant.infrastructure.storage.local import LocalFileStorage
from office_assistant.repositories.pricing import PriceRepository


@dataclass(frozen=True, slots=True)
class ApplyResult:
    applied: int
    already_exists: int
    needs_admin: int
    failed: int


class ExtractionReviewService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.products = ProductService(session_factory)
        self.customers = CustomerService(session_factory)
        self.pricing = PricingService(session_factory)
        self.payments = PaymentService(session_factory)
        self.events = BusinessEventService(session_factory)
        self.proposals = ProposalService(session_factory)

    async def summary(self, actor_telegram_user_id: int, run_id: UUID) -> dict[str, object]:
        async with self.session_factory() as session:
            run, _ = await self._authorized(session, actor_telegram_user_id, run_id)
            return await review_summary(session, run.id)

    async def problems(
        self, actor_telegram_user_id: int, run_id: UUID, *, offset: int = 0, limit: int = 10
    ) -> list[StructuredExtractionCandidate]:
        async with self.session_factory() as session:
            run, _ = await self._authorized(session, actor_telegram_user_id, run_id)
            statement = (
                select(StructuredExtractionCandidate)
                .where(
                    StructuredExtractionCandidate.extraction_run_id == run.id,
                    StructuredExtractionCandidate.status.in_(
                        [
                            CandidateStatus.NEEDS_REVIEW,
                            CandidateStatus.CONFLICT,
                            CandidateStatus.FAILED,
                        ]
                    ),
                )
                .order_by(StructuredExtractionCandidate.created_at)
                .offset(offset)
                .limit(limit)
            )
            return list((await session.execute(statement)).scalars().all())

    async def reject(self, actor_telegram_user_id: int, run_id: UUID) -> None:
        async with self.session_factory.begin() as session:
            run, actor = await self._authorized(
                session, actor_telegram_user_id, run_id, for_update=True
            )
            self._require_not_expired(run, actor)
            if run.review_status == ExtractionReviewStatus.REJECTED:
                return
            if run.review_status in {
                ExtractionReviewStatus.APPLIED,
                ExtractionReviewStatus.PARTIAL,
            }:
                raise DomainConflict("Applied extraction cannot be rejected")
            run.review_status = ExtractionReviewStatus.REJECTED
            run.reviewed_at = datetime.now(UTC)
            run.reviewed_by_user_id = actor.id
            candidates = list(
                (
                    await session.execute(
                        select(StructuredExtractionCandidate).where(
                            StructuredExtractionCandidate.extraction_run_id == run.id,
                            StructuredExtractionCandidate.status.in_(
                                [CandidateStatus.VALIDATED, CandidateStatus.NEEDS_REVIEW]
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for candidate in candidates:
                candidate.status = CandidateStatus.REJECTED
                candidate.reviewed_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    actor_user_id=actor.id,
                    action="STRUCTURED_REVIEW_REJECTED",
                    entity_type="structured_extraction_run",
                    entity_id=str(run.id),
                )
            )

    async def approve(self, actor_telegram_user_id: int, run_id: UUID) -> ApplyResult:
        async with self.session_factory.begin() as session:
            run, actor = await self._authorized(
                session, actor_telegram_user_id, run_id, for_update=True
            )
            self._require_not_expired(run, actor)
            if run.review_status == ExtractionReviewStatus.APPLIED:
                return await self._result(session, run.id)
            if run.review_status == ExtractionReviewStatus.REJECTED:
                raise DomainConflict("Rejected extraction cannot be applied")
            if run.review_status == ExtractionReviewStatus.APPLYING:
                return await self._result(session, run.id)
            run.review_status = ExtractionReviewStatus.APPLYING
            run.reviewed_by_user_id = actor.id
            run.reviewed_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    actor_user_id=actor.id,
                    action="STRUCTURED_REVIEW_APPROVED",
                    entity_type="structured_extraction_run",
                    entity_id=str(run.id),
                )
            )
        await self._apply_safe(actor_telegram_user_id, run_id)
        async with self.session_factory.begin() as session:
            run = await session.get(StructuredExtractionRun, run_id, with_for_update=True)
            if run is None:
                raise DomainConflict("Extraction run disappeared")
            result = await self._result(session, run.id)
            run.review_status = (
                ExtractionReviewStatus.PARTIAL
                if result.needs_admin or result.failed
                else ExtractionReviewStatus.APPLIED
            )
            return result

    async def request_reextraction(
        self, actor_telegram_user_id: int, ingestion_session_id: int
    ) -> StructuredExtractionJob:
        async with self.session_factory.begin() as session:
            actor = await require_actor(session, actor_telegram_user_id, admin=True)
            job = StructuredExtractionJob(
                ingestion_session_id=ingestion_session_id,
                requested_by_user_id=actor.id,
                status=ExtractionJobStatus.PENDING,
            )
            session.add(job)
            await session.flush()
            session.add(
                AuditEvent(
                    actor_user_id=actor.id,
                    action="STRUCTURED_REEXTRACTION_REQUESTED",
                    entity_type="ingestion_session",
                    entity_id=str(ingestion_session_id),
                    event_metadata={"extraction_job_id": job.id},
                )
            )
            return job

    async def _apply_safe(self, actor_telegram_user_id: int, run_id: UUID) -> None:
        async with self.session_factory() as session:
            run = await session.get(StructuredExtractionRun, run_id)
            if run is None:
                return
            sources, _ = await load_extraction_sources(
                session, self.storage, run.ingestion_session_id
            )
            candidates = list(
                (
                    await session.execute(
                        select(StructuredExtractionCandidate)
                        .where(StructuredExtractionCandidate.extraction_run_id == run_id)
                        .order_by(StructuredExtractionCandidate.created_at)
                    )
                )
                .scalars()
                .all()
            )
        source_map = {item.source_id: item for item in sources}
        ordered = sorted(
            candidates,
            key=lambda item: {
                "PRODUCT": 0,
                "CUSTOMER": 0,
                "PRODUCT_ALIAS": 1,
                "CUSTOMER_ALIAS": 1,
            }.get(item.candidate_type, 2),
        )
        for candidate in ordered:
            if candidate.candidate_type in {"PROPOSAL", "PROPOSAL_ITEM"}:
                continue
            if candidate.status != CandidateStatus.VALIDATED:
                continue
            try:
                entity_type, entity_id, no_change = await self._apply_one(
                    actor_telegram_user_id, candidate, ordered, source_map
                )
            except (DomainConflict, ValueError, KeyError):
                await self._mark(candidate.id, CandidateStatus.CONFLICT)
                continue
            await self._mark(
                candidate.id,
                CandidateStatus.ALREADY_EXISTS if no_change else CandidateStatus.APPLIED,
                entity_type,
                entity_id,
            )
            candidate.status = (
                CandidateStatus.ALREADY_EXISTS if no_change else CandidateStatus.APPLIED
            )
            candidate.applied_entity_id = entity_id
        await self._apply_proposal_groups(actor_telegram_user_id, ordered, source_map)

    async def _apply_proposal_groups(
        self,
        actor_telegram_user_id: int,
        candidates: list[StructuredExtractionCandidate],
        source_map: dict[str, ExtractionSource],
    ) -> None:
        proposals = [
            item
            for item in candidates
            if item.candidate_type == "PROPOSAL" and item.status == CandidateStatus.VALIDATED
        ]
        for proposal in proposals:
            local_ref = str(proposal.payload.get("local_ref", ""))
            items = [
                item
                for item in candidates
                if item.candidate_type == "PROPOSAL_ITEM"
                and item.status == CandidateStatus.VALIDATED
                and item.payload.get("proposal_ref") == local_ref
            ]
            try:
                proposal_date = date.fromisoformat(str(proposal.payload["proposal_date"]))
                customer_id = await self._customer_id(
                    str(proposal.payload["customer_ref"]), candidates
                )
                item_inputs: list[ProposalItemInput] = []
                for item in items:
                    product_ref = _optional(item.payload, "product_ref")
                    product_id = (
                        await self._product_id(product_ref, candidates) if product_ref else None
                    )
                    item_inputs.append(
                        ProposalItemInput(
                            position=int(str(item.payload["position"])),
                            product_id=product_id,
                            raw_product_name=_optional(item.payload, "raw_product_name"),
                            quantity=_decimal(item.payload.get("quantity")),
                            unit_price=_decimal(item.payload.get("unit_price")),
                            currency=_optional(item.payload, "currency"),
                            line_total=_decimal(item.payload.get("line_total")),
                            provenance=_provenance(item.source_refs, source_map),
                        )
                    )
                primary_source = source_map[proposal.source_refs[0]]
                documents = (
                    [(primary_source.file_id, ProposalDocumentRelation.PRIMARY)]
                    if primary_source.file_id is not None
                    else []
                )
                created = await self.proposals.create_proposal(
                    actor_telegram_user_id,
                    customer_id=customer_id,
                    proposal_date=proposal_date,
                    status=ProposalStatus(str(proposal.payload["status"])),
                    provenance=_provenance(proposal.source_refs, source_map),
                    proposal_number=_optional(proposal.payload, "proposal_number"),
                    notes=_optional(proposal.payload, "notes"),
                    documents=documents,
                    items=item_inputs,
                )
            except (DomainConflict, ValueError, KeyError):
                await self._mark(proposal.id, CandidateStatus.CONFLICT)
                for item in items:
                    await self._mark(item.id, CandidateStatus.CONFLICT)
                continue
            await self._mark(proposal.id, CandidateStatus.APPLIED, "proposal", str(created.id))
            for item in items:
                await self._mark(item.id, CandidateStatus.APPLIED, "proposal", str(created.id))

    async def _apply_one(
        self,
        actor_telegram_user_id: int,
        candidate: StructuredExtractionCandidate,
        all_candidates: list[StructuredExtractionCandidate],
        source_map: dict[str, ExtractionSource],
    ) -> tuple[str, str, bool]:
        payload = candidate.payload
        provenance = _provenance(candidate.source_refs, source_map)
        kind = candidate.candidate_type
        if kind == "PRODUCT":
            existing = await self.products.find_product_by_code(str(payload["product_code"]))
            if existing is not None:
                return "product", str(existing.id), True
            created = await self.products.create_product(
                actor_telegram_user_id,
                display_code=str(payload["product_code"]),
                name=str(payload["name"]),
                description=_optional(payload, "description"),
                provenance=provenance,
            )
            return "product", str(created.id), False
        if kind == "CUSTOMER":
            existing = await self.customers.find_customer_by_name(str(payload["display_name"]))
            if existing is not None:
                return "customer", str(existing.id), True
            created = await self.customers.create_customer(
                actor_telegram_user_id,
                display_name=str(payload["display_name"]),
                legal_name=_optional(payload, "legal_name"),
                provenance=provenance,
            )
            return "customer", str(created.id), False
        if kind == "PRODUCT_ALIAS":
            product_id = await self._product_id(str(payload["product_ref"]), all_candidates)
            existing = await self.products.search_product_by_alias(str(payload["alias"]))
            if existing is not None:
                return "product_alias", str(existing.id), True
            alias = await self.products.add_alias(
                actor_telegram_user_id,
                product_id,
                str(payload["alias"]),
                provenance=provenance,
            )
            return "product_alias", str(alias.id), False
        if kind == "CUSTOMER_ALIAS":
            customer_id = await self._customer_id(str(payload["customer_ref"]), all_candidates)
            existing = await self.customers.search_customer_by_alias(str(payload["alias"]))
            if existing is not None:
                return "customer_alias", str(existing.id), True
            alias = await self.customers.add_alias(
                actor_telegram_user_id,
                customer_id,
                str(payload["alias"]),
                provenance=provenance,
            )
            return "customer_alias", str(alias.id), False
        if kind == "PRICE":
            product_id = await self._product_id(str(payload["product_ref"]), all_candidates)
            customer_ref = _optional(payload, "customer_ref")
            customer_id = (
                await self._customer_id(customer_ref, all_candidates) if customer_ref else None
            )
            effective = _datetime(payload.get("effective_at")) or candidate.created_at
            amount = Decimal(str(payload["amount"]))
            async with self.session_factory() as session:
                history = await PriceRepository(session).history(
                    product_id=product_id,
                    customer_id=customer_id,
                    from_date=effective,
                    to_date=effective,
                )
            identical = next(
                (
                    item
                    for item in history
                    if item.amount == amount and item.currency == str(payload["currency"]).upper()
                ),
                None,
            )
            if identical is not None:
                return "product_price", str(identical.id), True
            if history:
                raise DomainConflict("Price conflict requires administrator review")
            created = await self.pricing.add_price(
                actor_telegram_user_id,
                product_id=product_id,
                customer_id=customer_id,
                amount=amount,
                currency=str(payload["currency"]),
                effective_at=effective,
                provenance=provenance,
            )
            return "product_price", str(created.id), False
        if kind == "PAYMENT":
            paid_at = _datetime(payload.get("paid_at"))
            if paid_at is None:
                raise ValueError("Payment date is required")
            customer_id = await self._customer_id(str(payload["customer_ref"]), all_candidates)
            created = await self.payments.create_payment(
                actor_telegram_user_id,
                customer_id=customer_id,
                amount=Decimal(str(payload["amount"])),
                currency=str(payload["currency"]),
                paid_at=paid_at,
                provenance=provenance,
                description=_optional(payload, "description"),
            )
            return "payment", str(created.id), False
        if kind == "BUSINESS_EVENT":
            occurred = _datetime(payload.get("occurred_at"))
            if occurred is None:
                raise ValueError("Event date is required")
            customer_id = await self._customer_id(str(payload["customer_ref"]), all_candidates)
            created = await self.events.create_event(
                actor_telegram_user_id,
                customer_id=customer_id,
                event_type=BusinessEventType(str(payload["event_type"])),
                occurred_at=occurred,
                summary=str(payload["summary"]),
                details=_optional(payload, "details"),
                provenance=provenance,
            )
            return "business_event", str(created.id), False
        raise ValueError("Candidate type requires grouped or administrative review")

    async def _product_id(
        self, reference: str, candidates: list[StructuredExtractionCandidate]
    ) -> UUID:
        existing = await self.products.find_product_by_code(reference)
        existing = existing or await self.products.search_product_by_alias(reference)
        if existing is not None:
            return existing.id
        return _applied_local_id(reference, "PRODUCT", candidates)

    async def _customer_id(
        self, reference: str, candidates: list[StructuredExtractionCandidate]
    ) -> UUID:
        existing = await self.customers.find_customer_by_name(reference)
        existing = existing or await self.customers.search_customer_by_alias(reference)
        if existing is not None:
            return existing.id
        return _applied_local_id(reference, "CUSTOMER", candidates)

    async def _mark(
        self,
        candidate_id: UUID,
        status: CandidateStatus,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> None:
        async with self.session_factory.begin() as session:
            row = await session.get(
                StructuredExtractionCandidate, candidate_id, with_for_update=True
            )
            if row is None or row.status in {
                CandidateStatus.APPLIED,
                CandidateStatus.ALREADY_EXISTS,
            }:
                return
            row.status = status
            row.applied_entity_type = entity_type
            row.applied_entity_id = entity_id
            row.reviewed_at = datetime.now(UTC)
            if status in {CandidateStatus.APPLIED, CandidateStatus.ALREADY_EXISTS}:
                row.applied_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    actor_user_id=None,
                    action=(
                        "EXTRACTION_CANDIDATE_APPLIED"
                        if status == CandidateStatus.APPLIED
                        else "EXTRACTION_CANDIDATE_UNRESOLVED"
                    ),
                    entity_type="structured_extraction_candidate",
                    entity_id=str(row.id),
                    event_metadata={"status": status, "applied_entity_id": entity_id},
                )
            )

    async def _authorized(
        self,
        session: AsyncSession,
        actor_telegram_user_id: int,
        run_id: UUID,
        *,
        for_update: bool = False,
    ) -> tuple[StructuredExtractionRun, User]:
        actor = await require_actor(session, actor_telegram_user_id)
        statement = select(StructuredExtractionRun).where(StructuredExtractionRun.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        run = (await session.execute(statement)).scalar_one_or_none()
        if run is None:
            raise DomainConflict("Extraction run does not exist")
        if run.owner_user_id != actor.id and not is_admin(actor):
            raise AuthorizationDenied("Only the owner or an administrator may review this run")
        return run, actor

    async def _result(self, session: AsyncSession, run_id: UUID) -> ApplyResult:
        statuses = list(
            (
                await session.execute(
                    select(StructuredExtractionCandidate.status).where(
                        StructuredExtractionCandidate.extraction_run_id == run_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return ApplyResult(
            applied=statuses.count(CandidateStatus.APPLIED),
            already_exists=statuses.count(CandidateStatus.ALREADY_EXISTS),
            needs_admin=sum(
                value in {CandidateStatus.CONFLICT, CandidateStatus.NEEDS_REVIEW}
                for value in statuses
            ),
            failed=statuses.count(CandidateStatus.FAILED),
        )

    @staticmethod
    def _require_not_expired(run: StructuredExtractionRun, actor: User) -> None:
        if run.review_expires_at <= datetime.now(UTC) and not is_admin(actor):
            raise AuthorizationDenied("Review has expired; an administrator may still inspect it")


def _provenance(refs: list[str], source_map: dict[str, ExtractionSource]) -> ProvenanceInput:
    sources: list[ExtractionSource] = []
    for source_id in refs:
        source = source_map.get(source_id)
        if source is None:
            source = source_map.get(re.sub(r"P\d+$", "", source_id))
        if source is not None:
            sources.append(source)
    if not sources:
        raise ValueError("Candidate has no valid provenance")
    primary = sources[0]
    return ProvenanceInput(
        SourceType.FUTURE_AI_EXTRACTION,
        file_id=primary.file_id,
        ingestion_session_id=primary.ingestion_session_id,
        ingestion_session_item_id=primary.ingestion_session_item_id,
        document_parse_run_id=primary.parse_run_id,
        locator={
            "primary": primary.locator,
            "sources": [{"source_id": item.source_id, "locator": item.locator} for item in sources],
        },
        description="Phase 8 user-approved structured extraction",
    )


def _applied_local_id(
    reference: str, candidate_type: str, candidates: list[StructuredExtractionCandidate]
) -> UUID:
    matches = [
        item
        for item in candidates
        if item.candidate_type == candidate_type
        and item.payload.get("local_ref") == reference
        and item.applied_entity_id
    ]
    if len(matches) != 1:
        raise DomainConflict("Entity reference is unresolved or ambiguous")
    return UUID(str(matches[0].applied_entity_id))


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _optional(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return str(value) if value is not None else None


def _decimal(value: object) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None
