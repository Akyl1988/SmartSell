from __future__ import annotations

"""
Kaspi catalog products synchronization service.

Fetches product catalog from Kaspi API and syncs to local database with idempotent upsert.
Tenant-scoped: each company has isolated catalog.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.kaspi_catalog_product import KaspiCatalogProduct
from app.services.kaspi_service import KaspiService

logger = get_logger(__name__)


async def sync_kaspi_catalog_products(
    session: AsyncSession,
    company_id: int,
    kaspi: KaspiService | None = None,
) -> dict[str, Any]:
    """
    Synchronize Kaspi catalog products to local database.

    Args:
        session: Async database session
        company_id: Company ID (tenant isolation)
        kaspi: KaspiService instance (if None, creates new one)

    Returns:
        Summary dict with keys: ok, company_id, fetched, inserted, updated
    """
    if kaspi is None:
        kaspi = KaspiService()

    fetched = 0
    inserted = 0
    updated = 0

    logger.info("Kaspi catalog sync start: company_id=%s", company_id)

    try:
        # Fetch products from Kaspi API (page 1, default page_size)
        items = await kaspi.get_products(page=1, page_size=100)
        fetched = len(items)

        logger.info("Kaspi catalog sync: company_id=%s fetched=%s", company_id, fetched)

        # Process each product item
        for item in items:
            # Extract offer_id (priority: offer_id, then id)
            offer_id = item.get("offer_id") or item.get("id")
            if not offer_id:
                logger.warning("Kaspi catalog sync: skipping item without offer_id/id")
                continue

            # Check if product already exists
            check_stmt = select(KaspiCatalogProduct.id).where(
                KaspiCatalogProduct.company_id == company_id,
                KaspiCatalogProduct.offer_id == str(offer_id),
            )
            result_check = await session.execute(check_stmt)
            exists = result_check.scalar_one_or_none()

            # Extract fields
            name = item.get("name") or item.get("title")
            sku = item.get("sku") or item.get("code")
            price = item.get("price")
            qty = item.get("qty") or item.get("quantity") or item.get("stock")
            is_active = item.get("is_active", True)

            # Build upsert statement (ON CONFLICT DO UPDATE)
            stmt = (
                insert(KaspiCatalogProduct)
                .values(
                    company_id=company_id,
                    offer_id=str(offer_id),
                    name=str(name) if name else None,
                    sku=str(sku) if sku else None,
                    price=float(price) if price is not None else None,
                    qty=int(qty) if qty is not None else None,
                    is_active=bool(is_active),
                    raw=item,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                .on_conflict_do_update(
                    index_elements=["company_id", "offer_id"],
                    set_={
                        "name": str(name) if name else None,
                        "sku": str(sku) if sku else None,
                        "price": float(price) if price is not None else None,
                        "qty": int(qty) if qty is not None else None,
                        "is_active": bool(is_active),
                        "raw": item,
                        "updated_at": datetime.utcnow(),
                    },
                )
            )

            await session.execute(stmt)

            # Track insert vs update
            if exists:
                updated += 1
            else:
                inserted += 1

        # Commit transaction
        await session.commit()

        logger.info(
            "Kaspi catalog sync success: company_id=%s fetched=%s inserted=%s updated=%s",
            company_id,
            fetched,
            inserted,
            updated,
        )

        return {
            "ok": True,
            "company_id": company_id,
            "fetched": fetched,
            "inserted": inserted,
            "updated": updated,
        }

    except Exception as e:
        logger.error("Kaspi catalog sync failed: company_id=%s error=%s", company_id, e)
        await session.rollback()
        raise
