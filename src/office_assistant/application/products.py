"""Product identity, aliases, lookup, and administrative deactivation."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from office_assistant.application.business_common import require_actor, validate_provenance
from office_assistant.application.errors import DomainConflict, EntityNotFound
from office_assistant.business.normalization import normalize_product_alias, normalize_product_code
from office_assistant.business.types import ProvenanceInput
from office_assistant.infrastructure.database.models import Product, ProductAlias, SourceType
from office_assistant.repositories.audit import AuditRepository
from office_assistant.repositories.products import ProductRepository
from office_assistant.repositories.provenance import ProvenanceRepository


class ProductService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def create_product(
        self,
        actor_telegram_user_id: int,
        *,
        display_code: str,
        name: str,
        description: str | None = None,
        provenance: ProvenanceInput | None = None,
    ) -> Product:
        canonical = normalize_product_code(display_code)
        alias_key = normalize_product_alias(display_code)
        try:
            async with self.session_factory.begin() as session:
                actor = await require_actor(session, actor_telegram_user_id)
                source_value = provenance or ProvenanceInput(SourceType.TELEGRAM_USER)
                validate_provenance(source_value, actor)
                source = ProvenanceRepository(session).add(source_value, actor_user_id=actor.id)
                await session.flush()
                repo = ProductRepository(session)
                await repo.lock_identity_namespace()
                if await repo.identity_conflict(
                    canonical_code=canonical, normalized_alias=alias_key
                ):
                    raise DomainConflict("Product code is already owned")
                product = repo.add(
                    canonical_code=canonical,
                    display_code=display_code.strip(),
                    name=name.strip(),
                    description=description,
                    actor_user_id=actor.id,
                    source_reference_id=source.id,
                )
                await session.flush()
                AuditRepository(session).add(
                    actor_user_id=actor.id,
                    action="PRODUCT_CREATED",
                    entity_type="product",
                    entity_id=str(product.id),
                )
                return product
        except IntegrityError as exc:
            raise DomainConflict("Product identity already exists") from exc

    async def add_alias(
        self,
        actor_telegram_user_id: int,
        product_id: UUID,
        alias: str,
        provenance: ProvenanceInput | None = None,
    ) -> ProductAlias:
        normalized = normalize_product_alias(alias)
        code_form = normalize_product_code(alias)
        try:
            async with self.session_factory.begin() as session:
                actor = await require_actor(session, actor_telegram_user_id)
                source_value = provenance or ProvenanceInput(SourceType.TELEGRAM_USER)
                validate_provenance(source_value, actor)
                source = ProvenanceRepository(session).add(source_value, actor_user_id=actor.id)
                await session.flush()
                repo = ProductRepository(session)
                await repo.lock_identity_namespace()
                product = await repo.get(product_id)
                if product is None:
                    raise EntityNotFound("Product does not exist")
                if await repo.identity_conflict(
                    canonical_code=code_form, normalized_alias=normalized, exclude_id=product_id
                ):
                    raise DomainConflict("Product alias is already owned")
                item = repo.add_alias(
                    product_id=product_id,
                    alias=alias.strip(),
                    normalized_alias=normalized,
                    actor_user_id=actor.id,
                    source_reference_id=source.id,
                )
                await session.flush()
                AuditRepository(session).add(
                    actor_user_id=actor.id,
                    action="PRODUCT_ALIAS_ADDED",
                    entity_type="product_alias",
                    entity_id=str(item.id),
                    metadata={"product_id": str(product_id)},
                )
                return item
        except IntegrityError as exc:
            raise DomainConflict("Product alias already exists") from exc

    async def find_product_by_code(self, value: str) -> Product | None:
        async with self.session_factory() as session:
            return await ProductRepository(session).find_by_code(normalize_product_code(value))

    async def search_product_by_alias(self, value: str) -> Product | None:
        async with self.session_factory() as session:
            return await ProductRepository(session).find_by_alias(normalize_product_alias(value))

    async def get_product(self, product_id: UUID) -> Product | None:
        async with self.session_factory() as session:
            return await ProductRepository(session).get(product_id)

    async def deactivate_product(self, actor_telegram_user_id: int, product_id: UUID) -> Product:
        async with self.session_factory.begin() as session:
            actor = await require_actor(session, actor_telegram_user_id, admin=True)
            product = await ProductRepository(session).get(product_id, for_update=True)
            if product is None:
                raise EntityNotFound("Product does not exist")
            if product.is_active:
                product.is_active = False
                product.deactivated_at = datetime.now(UTC)
                product.deactivated_by_user_id = actor.id
                AuditRepository(session).add(
                    actor_user_id=actor.id,
                    action="PRODUCT_DEACTIVATED",
                    entity_type="product",
                    entity_id=str(product.id),
                )
            return product
