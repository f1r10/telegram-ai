"""Authorized deterministic queries over authoritative PostgreSQL business data."""

import logging
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.errors import AuthorizationDenied
from office_assistant.business.normalization import (
    normalize_currency,
    normalize_customer_alias,
    normalize_customer_name,
    normalize_product_alias,
    normalize_product_code,
)
from office_assistant.business_tools.models import (
    BusinessSource,
    CompareProposalsInput,
    CustomerLookupView,
    CustomerView,
    EventHistoryInput,
    EventView,
    FindCustomerInput,
    FindProductInput,
    GetCustomerInput,
    GetProductInput,
    LatestProposalInput,
    MatchedProposalItem,
    Page,
    PaymentHistoryInput,
    PaymentTotalsInput,
    PaymentTotalsView,
    PaymentView,
    PriceHistoryInput,
    PriceResolutionView,
    PriceView,
    ProductLookupView,
    ProductView,
    ProposalComparison,
    ProposalDetails,
    ProposalDetailsInput,
    ProposalDocumentView,
    ProposalHistoryInput,
    ProposalItemView,
    ProposalSummary,
    ResolvePriceInput,
    ToolExecutionContext,
    ToolResult,
    ToolStatus,
)
from office_assistant.infrastructure.database.models import (
    BusinessEvent,
    Customer,
    CustomerAlias,
    FileObject,
    Payment,
    Product,
    ProductAlias,
    ProductPrice,
    Proposal,
    ProposalDocument,
    ProposalItem,
    SourceReference,
    User,
    UserRole,
    UserStatus,
)
from office_assistant.repositories.pricing import PriceRepository

logger = logging.getLogger(__name__)


class BusinessQueryService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def find_product(
        self, context: ToolExecutionContext, value: FindProductInput
    ) -> ToolResult[ProductLookupView]:
        async with self.session_factory() as session:
            actor = await self._actor(session, context)
            self._allow_inactive(actor, value.include_inactive)
            matches = await self._products(session, value.code_or_alias, value.include_inactive)
            return await self._identity_result(session, matches)

    async def get_product(
        self, context: ToolExecutionContext, value: GetProductInput
    ) -> ToolResult[ProductView]:
        async with self.session_factory() as session:
            actor = await self._actor(session, context)
            self._allow_inactive(actor, value.include_inactive)
            product = await session.get(Product, value.product_id)
            if product is None or (not value.include_inactive and not product.is_active):
                return ToolResult(status=ToolStatus.NOT_FOUND)
            view = self._product_view(product, "ID")
            return ToolResult(
                status=ToolStatus.OK,
                data=view,
                sources=[await self._source(session, product.source_reference_id)],
            )

    async def find_customer(
        self, context: ToolExecutionContext, value: FindCustomerInput
    ) -> ToolResult[CustomerLookupView]:
        async with self.session_factory() as session:
            actor = await self._actor(session, context)
            self._allow_inactive(actor, value.include_inactive)
            matches = await self._customers(session, value.name_or_alias, value.include_inactive)
            return await self._customer_identity_result(session, matches)

    async def get_customer(
        self, context: ToolExecutionContext, value: GetCustomerInput
    ) -> ToolResult[CustomerView]:
        async with self.session_factory() as session:
            actor = await self._actor(session, context)
            self._allow_inactive(actor, value.include_inactive)
            customer = await session.get(Customer, value.customer_id)
            if customer is None or (not value.include_inactive and not customer.is_active):
                return ToolResult(status=ToolStatus.NOT_FOUND)
            view = self._customer_view(customer, "ID")
            return ToolResult(
                status=ToolStatus.OK,
                data=view,
                sources=[await self._source(session, customer.source_reference_id)],
            )

    async def resolve_product_price(
        self, context: ToolExecutionContext, value: ResolvePriceInput
    ) -> ToolResult[PriceResolutionView]:
        async with self.session_factory() as session:
            await self._actor(session, context)
            product_result = await self._products(session, value.product, False)
            if len(product_result) != 1:
                return ToolResult(
                    status=(ToolStatus.NOT_FOUND if not product_result else ToolStatus.AMBIGUOUS),
                    warnings=["product_not_resolved"],
                )
            product = product_result[0][0]
            customer: Customer | None = None
            if value.customer:
                customer_result = await self._customers(session, value.customer, False)
                if len(customer_result) != 1:
                    return ToolResult(
                        status=(
                            ToolStatus.NOT_FOUND if not customer_result else ToolStatus.AMBIGUOUS
                        ),
                        warnings=["customer_not_resolved"],
                    )
                customer = customer_result[0][0]
            instant = value.as_of or datetime.now(UTC)
            candidates = await PriceRepository(session).applicable(
                product_id=product.id,
                customer_id=customer.id if customer else None,
                at=instant,
            )
            fallback = False
            if not candidates and customer and value.allow_general_fallback:
                candidates = await PriceRepository(session).applicable(
                    product_id=product.id, customer_id=None, at=instant
                )
                fallback = bool(candidates)
            if not candidates:
                return ToolResult(status=ToolStatus.NOT_FOUND)
            latest_at = candidates[0].effective_at
            latest = [item for item in candidates if item.effective_at == latest_at]
            views = [
                await self._price_view(session, item, product, customer, fallback)
                for item in latest
            ]
            sources = [item.source for item in views]
            if len(views) > 1:
                return ToolResult(
                    status=ToolStatus.CONFLICT,
                    data=PriceResolutionView(conflicting_prices=views),
                    sources=sources,
                )
            warnings = ["general_price_fallback_used"] if fallback else []
            return ToolResult(
                status=ToolStatus.OK,
                data=PriceResolutionView(price=views[0]),
                warnings=warnings,
                sources=sources,
            )

    async def get_product_price_history(
        self, context: ToolExecutionContext, value: PriceHistoryInput
    ) -> ToolResult[Page[PriceView]]:
        async with self.session_factory() as session:
            await self._actor(session, context)
            products = await self._products(session, value.product, False)
            if len(products) != 1:
                return ToolResult(
                    status=ToolStatus.NOT_FOUND if not products else ToolStatus.AMBIGUOUS
                )
            product = products[0][0]
            customer: Customer | None = None
            if value.customer:
                customers = await self._customers(session, value.customer, False)
                if len(customers) != 1:
                    return ToolResult(
                        status=ToolStatus.NOT_FOUND if not customers else ToolStatus.AMBIGUOUS
                    )
                customer = customers[0][0]
            statement = select(ProductPrice).where(ProductPrice.product_id == product.id)
            statement = statement.where(
                ProductPrice.customer_id == (customer.id if customer else None)
            )
            if value.from_date:
                statement = statement.where(ProductPrice.effective_at >= value.from_date)
            if value.to_date:
                statement = statement.where(ProductPrice.effective_at <= value.to_date)
            statement = (
                statement.order_by(
                    ProductPrice.effective_at.desc(),
                    ProductPrice.created_at.desc(),
                    ProductPrice.id,
                )
                .offset(value.offset)
                .limit(value.limit + 1)
            )
            rows = list((await session.execute(statement)).scalars().all())
            page_rows, has_more = rows[: value.limit], len(rows) > value.limit
            items = [
                await self._price_view(session, row, product, customer, False) for row in page_rows
            ]
            return ToolResult(
                status=ToolStatus.OK if items else ToolStatus.NOT_FOUND,
                data=Page(
                    items=items,
                    has_more=has_more,
                    next_offset=value.offset + value.limit if has_more else None,
                ),
                sources=[item.source for item in items],
            )

    async def get_customer_payments(
        self, context: ToolExecutionContext, value: PaymentHistoryInput
    ) -> ToolResult[Page[PaymentView]]:
        async with self.session_factory() as session:
            await self._actor(session, context)
            customer_result = await self._customers(session, value.customer, False)
            if len(customer_result) != 1:
                return ToolResult(
                    status=ToolStatus.NOT_FOUND if not customer_result else ToolStatus.AMBIGUOUS
                )
            customer = customer_result[0][0]
            statement = select(Payment).where(
                Payment.customer_id == customer.id, Payment.is_active.is_(True)
            )
            if value.from_date:
                statement = statement.where(Payment.paid_at >= value.from_date)
            if value.to_date:
                statement = statement.where(Payment.paid_at <= value.to_date)
            if value.currency:
                statement = statement.where(Payment.currency == normalize_currency(value.currency))
            statement = (
                statement.order_by(Payment.paid_at.desc(), Payment.id)
                .offset(value.offset)
                .limit(value.limit + 1)
            )
            rows = list((await session.execute(statement)).scalars().all())
            page_rows, has_more = rows[: value.limit], len(rows) > value.limit
            items = [await self._payment_view(session, row) for row in page_rows]
            return ToolResult(
                status=ToolStatus.OK if items else ToolStatus.NOT_FOUND,
                data=Page(
                    items=items,
                    has_more=has_more,
                    next_offset=value.offset + value.limit if has_more else None,
                ),
                sources=[item.source for item in items],
            )

    async def get_customer_payment_totals(
        self, context: ToolExecutionContext, value: PaymentTotalsInput
    ) -> ToolResult[PaymentTotalsView]:
        async with self.session_factory() as session:
            await self._actor(session, context)
            customer_result = await self._customers(session, value.customer, False)
            if len(customer_result) != 1:
                return ToolResult(
                    status=ToolStatus.NOT_FOUND if not customer_result else ToolStatus.AMBIGUOUS
                )
            customer = customer_result[0][0]
            statement = select(
                Payment.currency, func.sum(Payment.amount), func.count(Payment.id)
            ).where(Payment.customer_id == customer.id, Payment.is_active.is_(True))
            if value.from_date:
                statement = statement.where(Payment.paid_at >= value.from_date)
            if value.to_date:
                statement = statement.where(Payment.paid_at <= value.to_date)
            rows = (await session.execute(statement.group_by(Payment.currency))).all()
            if not rows:
                return ToolResult(status=ToolStatus.NOT_FOUND)
            return ToolResult(
                status=ToolStatus.OK,
                data=PaymentTotalsView(
                    payment_count=sum(int(count) for _, _, count in rows),
                    totals={currency: total for currency, total, _ in rows},
                ),
            )

    async def get_customer_events(
        self, context: ToolExecutionContext, value: EventHistoryInput
    ) -> ToolResult[Page[EventView]]:
        async with self.session_factory() as session:
            await self._actor(session, context)
            customers = await self._customers(session, value.customer, False)
            if len(customers) != 1:
                return ToolResult(
                    status=ToolStatus.NOT_FOUND if not customers else ToolStatus.AMBIGUOUS
                )
            customer = customers[0][0]
            statement = select(BusinessEvent).where(BusinessEvent.customer_id == customer.id)
            if value.event_type:
                statement = statement.where(BusinessEvent.event_type == value.event_type.upper())
            if value.from_date:
                statement = statement.where(BusinessEvent.occurred_at >= value.from_date)
            if value.to_date:
                statement = statement.where(BusinessEvent.occurred_at <= value.to_date)
            statement = (
                statement.order_by(BusinessEvent.occurred_at.desc(), BusinessEvent.id)
                .offset(value.offset)
                .limit(value.limit + 1)
            )
            rows = list((await session.execute(statement)).scalars().all())
            page_rows, has_more = rows[: value.limit], len(rows) > value.limit
            items = [await self._event_view(session, row) for row in page_rows]
            return ToolResult(
                status=ToolStatus.OK if items else ToolStatus.NOT_FOUND,
                data=Page(
                    items=items,
                    has_more=has_more,
                    next_offset=value.offset + value.limit if has_more else None,
                ),
                sources=[item.source for item in items],
            )

    async def get_customer_proposals(
        self, context: ToolExecutionContext, value: ProposalHistoryInput
    ) -> ToolResult[Page[ProposalSummary]]:
        async with self.session_factory() as session:
            await self._actor(session, context)
            customers = await self._customers(session, value.customer, False)
            if len(customers) != 1:
                return ToolResult(
                    status=ToolStatus.NOT_FOUND if not customers else ToolStatus.AMBIGUOUS
                )
            customer = customers[0][0]
            statement = select(Proposal).where(
                Proposal.customer_id == customer.id, Proposal.is_active.is_(True)
            )
            if value.from_date:
                statement = statement.where(Proposal.proposal_date >= value.from_date)
            if value.to_date:
                statement = statement.where(Proposal.proposal_date <= value.to_date)
            statement = (
                statement.order_by(
                    Proposal.proposal_date.desc(), Proposal.created_at.desc(), Proposal.id
                )
                .offset(value.offset)
                .limit(value.limit + 1)
            )
            rows = list((await session.execute(statement)).scalars().all())
            page_rows, has_more = rows[: value.limit], len(rows) > value.limit
            items = [await self._proposal_summary(session, row) for row in page_rows]
            return ToolResult(
                status=ToolStatus.OK if items else ToolStatus.NOT_FOUND,
                data=Page(
                    items=items,
                    has_more=has_more,
                    next_offset=value.offset + value.limit if has_more else None,
                ),
                sources=[item.source for item in items],
            )

    async def get_latest_customer_proposal(
        self, context: ToolExecutionContext, value: LatestProposalInput
    ) -> ToolResult[ProposalSummary]:
        async with self.session_factory() as session:
            await self._actor(session, context)
            customers = await self._customers(session, value.customer, False)
            if len(customers) != 1:
                return ToolResult(
                    status=ToolStatus.NOT_FOUND if not customers else ToolStatus.AMBIGUOUS
                )
            rows = list(
                (
                    await session.execute(
                        select(Proposal)
                        .where(
                            Proposal.customer_id == customers[0][0].id,
                            Proposal.is_active.is_(True),
                        )
                        .order_by(Proposal.proposal_date.desc(), Proposal.created_at.desc())
                        .limit(2)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return ToolResult(status=ToolStatus.NOT_FOUND)
            if len(rows) == 2 and (rows[0].proposal_date, rows[0].created_at) == (
                rows[1].proposal_date,
                rows[1].created_at,
            ):
                return ToolResult(status=ToolStatus.AMBIGUOUS, warnings=["latest_order_tie"])
            item = await self._proposal_summary(session, rows[0])
            return ToolResult(status=ToolStatus.OK, data=item, sources=[item.source])

    async def get_proposal_details(
        self, context: ToolExecutionContext, value: ProposalDetailsInput
    ) -> ToolResult[ProposalDetails]:
        async with self.session_factory() as session:
            await self._actor(session, context)
            details = await self._proposal_details(session, value.proposal_id)
            if details is None:
                return ToolResult(status=ToolStatus.NOT_FOUND)
            sources = [details.proposal.source, *(item.source for item in details.items)]
            return ToolResult(status=ToolStatus.OK, data=details, sources=sources)

    async def compare_proposals(
        self, context: ToolExecutionContext, value: CompareProposalsInput
    ) -> ToolResult[ProposalComparison]:
        async with self.session_factory() as session:
            await self._actor(session, context)
            first = await self._proposal_details(session, value.proposal_a)
            second = await self._proposal_details(session, value.proposal_b)
            if first is None or second is None:
                return ToolResult(status=ToolStatus.NOT_FOUND)
            comparison = compare_proposal_details(first, second)
            return ToolResult(
                status=ToolStatus.OK,
                data=comparison,
                warnings=comparison.warnings,
                sources=[first.proposal.source, second.proposal.source],
            )

    async def authorize(self, context: ToolExecutionContext) -> None:
        async with self.session_factory() as session:
            await self._actor(session, context)

    async def _actor(self, session: AsyncSession, context: ToolExecutionContext) -> User:
        actor = await session.get(User, context.user_id)
        if (
            actor is None
            or actor.status != UserStatus.ACTIVE
            or actor.role not in {UserRole.ADMIN, UserRole.USER}
            or str(actor.role) != context.role
        ):
            raise AuthorizationDenied("Business tools require a verified ACTIVE actor")
        return actor

    @staticmethod
    def _allow_inactive(actor: User, include_inactive: bool) -> None:
        if include_inactive and actor.role != UserRole.ADMIN:
            raise AuthorizationDenied("Only administrators may include inactive entities")

    async def _products(
        self, session: AsyncSession, value: str, include_inactive: bool
    ) -> list[tuple[Product, str]]:
        try:
            product_id = UUID(value)
        except ValueError:
            product_id = None
        by_id = await session.get(Product, product_id) if product_id else None
        code = normalize_product_code(value)
        alias = normalize_product_alias(value)
        statement = (
            select(Product, ProductAlias.id)
            .outerjoin(ProductAlias, ProductAlias.product_id == Product.id)
            .where((Product.canonical_code == code) | (ProductAlias.normalized_alias == alias))
        )
        if not include_inactive:
            statement = statement.where(Product.is_active.is_(True))
        rows = (await session.execute(statement)).all()
        found: dict[UUID, tuple[Product, str]] = {}
        if by_id is not None and (include_inactive or by_id.is_active):
            found[by_id.id] = (by_id, "ID")
        for product, alias_id in rows:
            matched = "CANONICAL_CODE" if product.canonical_code == code else "ALIAS"
            if alias_id is not None or matched == "CANONICAL_CODE":
                existing = found.get(product.id)
                if existing is None or matched == "CANONICAL_CODE":
                    found[product.id] = (product, matched)
        return list(found.values())

    async def _customers(
        self, session: AsyncSession, value: str, include_inactive: bool
    ) -> list[tuple[Customer, str]]:
        try:
            customer_id = UUID(value)
        except ValueError:
            customer_id = None
        by_id = await session.get(Customer, customer_id) if customer_id else None
        name = normalize_customer_name(value)
        alias = normalize_customer_alias(value)
        statement = (
            select(Customer, CustomerAlias.id)
            .outerjoin(CustomerAlias, CustomerAlias.customer_id == Customer.id)
            .where((Customer.normalized_name == name) | (CustomerAlias.normalized_alias == alias))
        )
        if not include_inactive:
            statement = statement.where(Customer.is_active.is_(True))
        rows = (await session.execute(statement)).all()
        found: dict[UUID, tuple[Customer, str]] = {}
        if by_id is not None and (include_inactive or by_id.is_active):
            found[by_id.id] = (by_id, "ID")
        for customer, alias_id in rows:
            matched = "CANONICAL_NAME" if customer.normalized_name == name else "ALIAS"
            if alias_id is not None or matched == "CANONICAL_NAME":
                existing = found.get(customer.id)
                if existing is None or matched == "CANONICAL_NAME":
                    found[customer.id] = (customer, matched)
        return list(found.values())

    async def _identity_result(
        self, session: AsyncSession, matches: list[tuple[Product, str]]
    ) -> ToolResult[ProductLookupView]:
        if not matches:
            return ToolResult(status=ToolStatus.NOT_FOUND)
        if len(matches) > 1:
            options = [self._product_view(item, matched) for item, matched in matches]
            return ToolResult(
                status=ToolStatus.AMBIGUOUS,
                data=ProductLookupView(candidates=options),
            )
        product, matched = matches[0]
        view = self._product_view(product, matched)
        return ToolResult(
            status=ToolStatus.OK,
            data=ProductLookupView(product=view),
            sources=[await self._source(session, product.source_reference_id)],
        )

    async def _customer_identity_result(
        self, session: AsyncSession, matches: list[tuple[Customer, str]]
    ) -> ToolResult[CustomerLookupView]:
        if not matches:
            return ToolResult(status=ToolStatus.NOT_FOUND)
        if len(matches) > 1:
            options = [self._customer_view(item, matched) for item, matched in matches]
            return ToolResult(
                status=ToolStatus.AMBIGUOUS,
                data=CustomerLookupView(candidates=options),
            )
        customer, matched = matches[0]
        view = self._customer_view(customer, matched)
        return ToolResult(
            status=ToolStatus.OK,
            data=CustomerLookupView(customer=view),
            sources=[await self._source(session, customer.source_reference_id)],
        )

    @staticmethod
    def _product_view(product: Product, matched: str) -> ProductView:
        return ProductView(
            product_id=product.id,
            display_code=product.display_code,
            canonical_code=product.canonical_code,
            name=product.name,
            description=product.description,
            is_active=product.is_active,
            matched_by=matched,  # type: ignore[arg-type]
        )

    @staticmethod
    def _customer_view(customer: Customer, matched: str) -> CustomerView:
        return CustomerView(
            customer_id=customer.id,
            display_name=customer.display_name,
            normalized_name=customer.normalized_name,
            legal_name=customer.legal_name,
            is_active=customer.is_active,
            matched_by=matched,  # type: ignore[arg-type]
        )

    async def _source(self, session: AsyncSession, source_id: UUID) -> BusinessSource:
        source = await session.get(SourceReference, source_id)
        if source is None:
            return BusinessSource(source_type="UNKNOWN")
        file = await session.get(FileObject, source.file_id) if source.file_id else None
        return BusinessSource(
            source_type=str(source.source_type),
            filename=file.original_filename if file else None,
            file_id=source.file_id,
            locator=source.locator,
            ingestion_session_id=source.ingestion_session_id,
        )

    async def _price_view(
        self,
        session: AsyncSession,
        row: ProductPrice,
        product: Product,
        customer: Customer | None,
        fallback: bool,
    ) -> PriceView:
        return PriceView(
            price_id=row.id,
            product=self._product_view(product, "ID"),
            customer=self._customer_view(customer, "ID") if customer and not fallback else None,
            amount=row.amount,
            currency=row.currency,
            effective_at=row.effective_at,
            valid_until=row.valid_until,
            price_kind=str(row.price_kind),
            used_general_fallback=fallback,
            source=await self._source(session, row.source_reference_id),
        )

    async def _payment_view(self, session: AsyncSession, row: Payment) -> PaymentView:
        return PaymentView(
            payment_id=row.id,
            customer_id=row.customer_id,
            amount=row.amount,
            currency=row.currency,
            paid_at=row.paid_at,
            description=row.description,
            source=await self._source(session, row.source_reference_id),
        )

    async def _event_view(self, session: AsyncSession, row: BusinessEvent) -> EventView:
        return EventView(
            event_id=row.id,
            customer_id=row.customer_id,
            event_type=str(row.event_type),
            occurred_at=row.occurred_at,
            summary=row.summary,
            details=row.details,
            source=await self._source(session, row.source_reference_id),
        )

    async def _proposal_summary(self, session: AsyncSession, row: Proposal) -> ProposalSummary:
        return ProposalSummary(
            proposal_id=row.id,
            customer_id=row.customer_id,
            proposal_number=row.proposal_number,
            proposal_date=row.proposal_date,
            status=str(row.status),
            notes=row.notes,
            created_at=row.created_at,
            source=await self._source(session, row.source_reference_id),
        )

    async def _proposal_details(
        self, session: AsyncSession, proposal_id: UUID
    ) -> ProposalDetails | None:
        proposal = await session.get(Proposal, proposal_id)
        if proposal is None or not proposal.is_active:
            return None
        rows = (
            await session.execute(
                select(ProposalItem, Product)
                .outerjoin(Product, Product.id == ProposalItem.product_id)
                .where(ProposalItem.proposal_id == proposal.id)
                .order_by(ProposalItem.position)
            )
        ).all()
        items = [
            ProposalItemView(
                item_id=item.id,
                position=item.position,
                product_id=item.product_id,
                product_code=product.display_code if product else None,
                raw_product_name=item.raw_product_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                currency=item.currency,
                line_total=item.line_total,
                source=await self._source(session, item.source_reference_id),
            )
            for item, product in rows
        ]
        document_rows = (
            await session.execute(
                select(ProposalDocument, FileObject)
                .join(FileObject, FileObject.id == ProposalDocument.file_id)
                .where(ProposalDocument.proposal_id == proposal.id)
                .order_by(ProposalDocument.relation_type, ProposalDocument.file_id)
            )
        ).all()
        documents = [
            ProposalDocumentView(
                file_id=document.file_id,
                filename=file.original_filename,
                relation_type=str(document.relation_type),
            )
            for document, file in document_rows
        ]
        return ProposalDetails(
            proposal=await self._proposal_summary(session, proposal),
            items=items,
            documents=documents,
        )


def compare_proposal_details(first: ProposalDetails, second: ProposalDetails) -> ProposalComparison:
    """Match unambiguous items and calculate second (after) minus first (before)."""

    first_keys = [_comparison_key(item) for item in first.items]
    second_keys = [_comparison_key(item) for item in second.items]
    first_counts = Counter(key for key in first_keys if key)
    second_counts = Counter(key for key in second_keys if key)
    second_by_key = {key: item for key, item in zip(second_keys, second.items, strict=True) if key}
    matched: list[MatchedProposalItem] = []
    only_a: list[ProposalItemView] = []
    used_b: set[UUID] = set()
    warnings: list[str] = []
    for key, item_a in zip(first_keys, first.items, strict=True):
        if not key or first_counts[key] != 1 or second_counts[key] != 1:
            only_a.append(item_a)
            if key and (first_counts[key] > 1 or second_counts[key] > 1):
                warnings.append(f"ambiguous_item_key:{key}")
            continue
        item_b = second_by_key[key]
        used_b.add(item_b.item_id)
        comparable = bool(
            item_a.currency and item_b.currency and item_a.currency == item_b.currency
        )
        matched.append(
            MatchedProposalItem(
                match_key=key,
                item_a=item_a,
                item_b=item_b,
                quantity_delta=_delta(item_b.quantity, item_a.quantity),
                unit_price_delta=(
                    _delta(item_b.unit_price, item_a.unit_price) if comparable else None
                ),
                line_total_delta=(
                    _delta(item_b.line_total, item_a.line_total) if comparable else None
                ),
                currency_comparable=comparable,
            )
        )
        if item_a.currency != item_b.currency:
            warnings.append(f"currency_mismatch:{key}")
    only_b = [item for item in second.items if item.item_id not in used_b]
    return ProposalComparison(
        before_proposal=first.proposal,
        after_proposal=second.proposal,
        delta_direction="after_minus_before",
        proposal_a=first.proposal,
        proposal_b=second.proposal,
        matched_items=matched,
        only_in_a=only_a,
        only_in_b=only_b,
        warnings=sorted(set(warnings)),
    )


def _comparison_key(item: ProposalItemView) -> str | None:
    if item.product_id:
        return f"product:{item.product_id}"
    if item.raw_product_name:
        return f"raw:{normalize_product_alias(item.raw_product_name)}"
    return None


def _delta(after: Decimal | None, before: Decimal | None) -> Decimal | None:
    return after - before if after is not None and before is not None else None
