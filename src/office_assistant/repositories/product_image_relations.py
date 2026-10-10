"""Persistence queries for the product ↔ image relation (Master Spec §23.6)."""

from uuid import UUID

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from office_assistant.infrastructure.database.models import (
    ImageAsset,
    Product,
    ProductImageRelation,
    ProductImageRelationConfirmation,
)


class ProductImageRelationRepository:
    """Works in the caller's transaction; the relation is written where it is decided.

    Every read asks for an *active* row, because history is kept rather than deleted (§23.6): a
    superseded claim still has to be provable later.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_confirmation_namespace(self) -> None:
        """Serialize confirmations, so two products cannot both claim one picture.

        A row lock would not help for content nobody has confirmed yet, because there is no row to
        lock — the same device the product identity writes use, and what makes §22.7 a decision
        rather than a race.
        """

        await self.session.execute(text("SELECT pg_advisory_xact_lock(71002)"))

    async def active_confirmation_holder_by_content(
        self, content_sha256: str
    ) -> tuple[UUID, str] | None:
        """Who already holds the explicit claim on these exact bytes, named by the code to say back.

        Keyed by content rather than by an asset id because a caption arrives with only the upload's
        hash: the check has to answer before any picture content is registered, and §22.7 must be a
        refusal at that moment rather than a correction later. At most one row can answer, and the
        partial unique index is what makes that a fact rather than a convention.
        """

        statement: Select[tuple[UUID, str]] = (
            select(ProductImageRelation.product_id, Product.display_code)
            .join(ImageAsset, ImageAsset.id == ProductImageRelation.image_asset_id)
            .join(Product, Product.id == ProductImageRelation.product_id)
            .where(
                ImageAsset.content_sha256 == content_sha256,
                ProductImageRelation.confirmation_type
                == ProductImageRelationConfirmation.USER_EXPLICIT,
                ProductImageRelation.superseded_at.is_(None),
            )
        )
        row = (await self.session.execute(statement)).first()
        return None if row is None else (row[0], row[1])

    def add(
        self,
        *,
        product_id: UUID,
        image_asset_id: UUID,
        role: str,
        is_reference: bool,
        confirmation_type: ProductImageRelationConfirmation,
        source_reference_id: UUID,
        created_by_user_id: int,
    ) -> ProductImageRelation:
        relation = ProductImageRelation(
            product_id=product_id,
            image_asset_id=image_asset_id,
            role=role,
            is_reference=is_reference,
            confirmation_type=str(confirmation_type),
            source_reference_id=source_reference_id,
            created_by_user_id=created_by_user_id,
        )
        self.session.add(relation)
        return relation
