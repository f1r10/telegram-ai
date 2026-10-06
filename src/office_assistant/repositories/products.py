"""Product and product-alias persistence queries."""

from uuid import UUID

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import Product, ProductAlias


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_identity_namespace(self) -> None:
        await self.session.execute(text("SELECT pg_advisory_xact_lock(71001)"))

    async def get(self, product_id: UUID, *, for_update: bool = False) -> Product | None:
        statement: Select[tuple[Product]] = select(Product).where(Product.id == product_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def find_by_code(
        self, canonical_code: str, *, active_only: bool = True
    ) -> Product | None:
        statement = select(Product).where(Product.canonical_code == canonical_code)
        if active_only:
            statement = statement.where(Product.is_active.is_(True))
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def find_by_alias(
        self, normalized_alias: str, *, active_only: bool = True
    ) -> Product | None:
        statement = (
            select(Product)
            .join(ProductAlias, ProductAlias.product_id == Product.id)
            .where(ProductAlias.normalized_alias == normalized_alias)
        )
        if active_only:
            statement = statement.where(Product.is_active.is_(True))
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def identity_conflict(
        self, *, canonical_code: str, normalized_alias: str, exclude_id: UUID | None = None
    ) -> bool:
        products = select(Product.id).where(Product.canonical_code == canonical_code)
        aliases = select(ProductAlias.product_id).where(
            ProductAlias.normalized_alias == normalized_alias
        )
        if exclude_id is not None:
            products = products.where(Product.id != exclude_id)
            aliases = aliases.where(ProductAlias.product_id != exclude_id)
        return (
            await self.session.execute(products.union_all(aliases).limit(1))
        ).first() is not None

    def add(
        self,
        *,
        canonical_code: str,
        display_code: str,
        name: str,
        description: str | None,
        actor_user_id: int,
        source_reference_id: UUID,
    ) -> Product:
        product = Product(
            canonical_code=canonical_code,
            display_code=display_code,
            name=name,
            description=description,
            is_active=True,
            created_by_user_id=actor_user_id,
            source_reference_id=source_reference_id,
        )
        self.session.add(product)
        return product

    def add_alias(
        self,
        *,
        product_id: UUID,
        alias: str,
        normalized_alias: str,
        actor_user_id: int,
        source_reference_id: UUID,
    ) -> ProductAlias:
        item = ProductAlias(
            product_id=product_id,
            alias=alias,
            normalized_alias=normalized_alias,
            created_by_user_id=actor_user_id,
            source_reference_id=source_reference_id,
        )
        self.session.add(item)
        return item
